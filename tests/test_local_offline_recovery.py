"""Offline one-time owner recovery: no HTTP reset or default secret.

The repository contains no generated recovery codes; tests use isolated
temporary owner files and redact any one-time tokens from logs.
"""
from contextlib import nullcontext, redirect_stdout
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_operator_auth import (
    LocalOperatorAuth, OperatorConfigurationError, RECOVERY_VERSION)
import local_server

OLD="test-owner-password-before-recovery-45"
NEW="test-owner-password-after-recovery-45"
ANOTHER="test-owner-password-rotated-once-45"


class OfflineOwnerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"operator.json"
        LocalOperatorAuth.provision(self.path,OLD)
        self.owner=LocalOperatorAuth.load(self.path)

    def enroll(self):
        code,result=self.owner.setup_recovery(OLD)
        self.assertEqual(200,code)
        return result["recovery_code"]

    def test_enroll_requires_existing_password_and_leaves_v1_unmodified_on_failure(self):
        original=self.path.read_bytes()
        status,result=self.owner.setup_recovery("incorrect-secret")
        self.assertEqual(401,status)
        self.assertNotIn("recovery_code",result)
        self.assertEqual(original,self.path.read_bytes())
        self.assertEqual(1,json.loads(self.path.read_text())["version"])

    def test_enroll_migrates_v1_to_recovery_v2_without_storing_plain_code(self):
        recovery=self.enroll()
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]{43}",recovery))
        record=json.loads(self.path.read_text())
        self.assertEqual(RECOVERY_VERSION,record["version"])
        self.assertEqual({"version","salt","scrypt_hash","recovery_salt","recovery_hash"},
                         set(record))
        self.assertNotIn(recovery,self.path.read_text())
        self.assertNotIn(OLD,self.path.read_text())
        self.assertEqual(200,LocalOperatorAuth.load(self.path).login(OLD)[0])

    def test_high_entropy_recovery_codes_are_unique_and_reenroll_replaces_code(self):
        first=self.enroll()
        second=self.enroll()
        self.assertNotEqual(first,second)
        self.assertEqual(403,self.owner.recover_with_code(first,NEW)[0])
        self.assertEqual(200,self.owner.recover_with_code(second,NEW)[0])

    def test_valid_code_resets_owner_and_is_consumed_atomically(self):
        code=self.enroll()
        session=self.owner.login(OLD)[1]
        status,result=self.owner.recover_with_code(code,NEW)
        self.assertEqual(200,status,result)
        self.assertEqual("OWNER_RECOVERED_CODE_CONSUMED",result["status"])
        self.assertIsNone(self.owner.inspect(session["cookie_token"]))
        self.assertEqual(401,self.owner.login(OLD)[0])
        self.assertEqual(200,self.owner.login(NEW)[0])
        after=LocalOperatorAuth.load(self.path)
        self.assertEqual(200,after.login(NEW)[0])
        self.assertEqual(401,after.login(OLD)[0])
        self.assertEqual(1,json.loads(self.path.read_text())["version"])
        self.assertEqual(403,after.recover_with_code(code,ANOTHER)[0])

    def test_invalid_wrong_and_malformed_codes_fail_without_changes(self):
        self.enroll()
        old=self.path.read_bytes()
        for bad in ("wrong", "x"*43, "", None, 42, True, "\N{SNOWMAN}"*43):
            status,result=self.owner.recover_with_code(bad,NEW)
            self.assertEqual(403,status,bad)
            self.assertNotIn("recovery_code",result)
            self.assertEqual(old,self.path.read_bytes())
        self.assertEqual(200,self.owner.login(OLD)[0])

    def test_recovery_not_enabled_is_denied_without_owner_modification(self):
        before=self.path.read_bytes()
        code,result=self.owner.recover_with_code("x"*43,NEW)
        self.assertEqual(403,code)
        self.assertEqual("recovery_not_enabled",result["error"])
        self.assertEqual(before,self.path.read_bytes())

    def test_new_password_requires_existing_minimum_strength_contract(self):
        recovery=self.enroll()
        before=self.path.read_bytes()
        for invalid in ("small", "", 444, True, None, "x"*129, "hello\nnew-password"):
            code,_=self.owner.recover_with_code(recovery,invalid)
            self.assertEqual(422,code,invalid)
            self.assertEqual(before,self.path.read_bytes())
        self.assertEqual(200,self.owner.recover_with_code(recovery,NEW)[0])

    def test_enrollment_storage_failure_does_not_change_owner_or_session(self):
        session=self.owner.login(OLD)[1]
        before=self.path.read_bytes()
        with patch("local_source_policy.secure_replace_json",
                   side_effect=OSError("synthetic-disk-full")):
            status,result=self.owner.setup_recovery(OLD)
        self.assertEqual(503,status)
        self.assertEqual("recovery_setup_storage_failed",result["error"])
        self.assertEqual(before,self.path.read_bytes())
        self.assertIsNotNone(self.owner.inspect(session["cookie_token"]))

    def test_recovery_storage_failure_preserves_recovery_proof_and_password(self):
        code=self.enroll()
        original=self.path.read_bytes()
        session=self.owner.login(OLD)[1]
        with patch("local_source_policy.secure_replace_json",
                   side_effect=OSError("synthetic-disk-full")):
            status,result=self.owner.recover_with_code(code,NEW)
        self.assertEqual(503,status)
        self.assertEqual("recovery_storage_failed",result["error"])
        self.assertEqual(original,self.path.read_bytes())
        self.assertIsNotNone(self.owner.inspect(session["cookie_token"]))
        self.assertEqual(200,LocalOperatorAuth.load(self.path).login(OLD)[0])
        self.assertEqual(200,self.owner.recover_with_code(code,NEW)[0])

    def test_password_rotation_revokes_previous_offline_recovery_code(self):
        code=self.enroll()
        self.assertEqual(200,self.owner.rotate_password(OLD,NEW)[0])
        reloaded=LocalOperatorAuth.load(self.path)
        self.assertEqual(1,json.loads(self.path.read_text())["version"])
        self.assertEqual(403,reloaded.recover_with_code(code,ANOTHER)[0])
        self.assertEqual(200,reloaded.login(NEW)[0])

    def test_corrupt_v2_recovery_verifier_and_wrong_schema_fail_closed(self):
        code=self.enroll()
        record=json.loads(self.path.read_text())
        record["recovery_hash"]="xx"
        self.path.write_text(json.dumps(record))
        with self.assertRaises(OperatorConfigurationError):
            LocalOperatorAuth.load(self.path)
        record["recovery_hash"]="00"*32
        record["unsupported"]="unexpected"
        self.path.write_text(json.dumps(record))
        with self.assertRaises(OperatorConfigurationError):
            LocalOperatorAuth.load(self.path)
        self.assertNotIn(code,self.path.read_text())

    def test_recovery_code_does_not_enable_http_reset_endpoint(self):
        from http.server import ThreadingHTTPServer
        from urllib.request import urlopen
        from urllib.error import HTTPError
        import threading
        from local_server import create_handler
        with ThreadingHTTPServer(("127.0.0.1",0),
                create_handler(operator_auth=self.owner)) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                import http.client
                conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
                conn.request("POST","/api/operator-recover",body=b"{}",
                             headers={"Content-Type":"application/json"})
                response=conn.getresponse()
                self.assertEqual(404,response.status)
                response.read()
                conn.close()
            finally:
                server.shutdown()
                thread.join(timeout=3)


class OfflineRecoveryCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        self.owner_path=self.home/".ai-automation-studio"/"operator.json"
        LocalOperatorAuth.provision(self.owner_path,OLD)

    def invoke(self,flags,answers):
        output=io.StringIO()
        with patch("local_server.Path.home",return_value=self.home), \
             patch("local_server.getpass.getpass",side_effect=answers), \
             patch("local_server._lock_recovery_to_offline_port",return_value=nullcontext()), \
             redirect_stdout(output):
            local_server.main(flags)
        return output.getvalue()

    def test_cli_enable_and_one_time_code_recovers_owner_without_http(self):
        output=self.invoke(["--enable-recovery"],[OLD])
        match=re.search(r"(?m)^([A-Za-z0-9_-]{43})$",output)
        self.assertIsNotNone(match)
        code=match.group(1)
        self.assertNotIn(OLD,output)
        record=self.owner_path.read_text()
        self.assertNotIn(code,record)
        recovery_output=self.invoke(["--recover-owner"],[code,NEW,NEW])
        self.assertIn("Recovery code consumed",recovery_output)
        self.assertNotIn(code,recovery_output)
        self.assertEqual(200,LocalOperatorAuth.load(self.owner_path).login(NEW)[0])
        self.assertEqual(401,LocalOperatorAuth.load(self.owner_path).login(OLD)[0])
        with self.assertRaises(SystemExit):
            self.invoke(["--recover-owner"],[code,ANOTHER,ANOTHER])

    def test_cli_enable_denies_wrong_old_password_without_stdout_code(self):
        output=io.StringIO()
        with patch("local_server.Path.home",return_value=self.home), \
             patch("local_server.getpass.getpass",return_value="wrong"), \
             patch("local_server._lock_recovery_to_offline_port",
                   return_value=nullcontext()), \
             redirect_stdout(output):
            with self.assertRaises(SystemExit):
                local_server.main(["--enable-recovery"])
        self.assertEqual("",output.getvalue())
        self.assertEqual(1,json.loads(self.owner_path.read_text())["version"])

    def test_cli_mismatching_passwords_rejected_and_code_not_consumed(self):
        code=re.search(r"(?m)^([A-Za-z0-9_-]{43})$",
                       self.invoke(["--enable-recovery"],[OLD])).group(1)
        with self.assertRaises(SystemExit):
            self.invoke(["--recover-owner"],[code,NEW,ANOTHER])
        self.assertEqual(RECOVERY_VERSION,
                         json.loads(self.owner_path.read_text())["version"])
        self.assertIn("consumed",
                      self.invoke(["--recover-owner"],[code,NEW,NEW]))

    def test_cli_stop_server_gate_precedes_all_recovery_prompts(self):
        fake=Mock()
        fake.bind.side_effect=OSError("port already bound")
        with patch("local_server.socket.socket",return_value=fake), \
             patch("local_server.getpass.getpass") as getpass_mock:
            with self.assertRaises(OperatorConfigurationError):
                local_server._lock_recovery_to_offline_port()
        self.assertTrue(fake.close.called)
        getpass_mock.assert_not_called()

    def test_cli_recovery_flags_are_mutually_exclusive(self):
        with redirect_stdout(io.StringIO()),self.assertRaises(SystemExit) as caught:
            with patch("sys.stderr",new=io.StringIO()):
                local_server.main(["--enable-recovery","--recover-owner"])
        self.assertEqual(2,caught.exception.code)


if __name__=="__main__":
    unittest.main()
