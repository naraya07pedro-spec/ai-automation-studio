"""Slice 51: model catalog, actual protocol orchestration, loopback restrictions."""
import http.server
import json
from pathlib import Path
import sys
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
import local_ollama_agent as agent
import local_ollama_readiness as ready


class ReadinessUnitTests(unittest.TestCase):
    def test_sanitized_catalog_sorted_deduplicated(self):
        result=ready.installed_models(lambda:{"models":[
            {"name":"test:1","digest":"NOT_FOR_CLIENT","details":{"secret":"test"}},
            {"name":"aaa:2"},{"name":"test:1"}]})
        self.assertEqual(["aaa:2","test:1"],result)

    def test_invalid_or_oversized_catalog_is_rejected(self):
        bad=[None,{},{"models":"hello"},{"models":[{"not_name":"x"}]},
             {"models":[{"name":True}]},{"models":[{"name":"foo bar"}]},
             {"models":[{"name":"bad/../model"}]},
             {"models":[{"name":"x"}]*65}]
        for entry in bad:
            with self.subTest(item=str(entry)[:45]),self.assertRaises(agent.AgentError):
                ready.installed_models(lambda entry=entry:entry)

    def test_probe_tool_call_observed_from_model_protocol(self):
        seen=[]
        def fake(payload):
            seen.append(payload)
            if len(seen)==1:
                self.assertEqual("fixture:1",payload["model"])
                self.assertEqual(False,payload["stream"])
                return {"message":{"content":"","tool_calls":[{
                    "function":{"name":"calculate","arguments":{
                        "a":"7","b":"8","operation":"multiply"}}}]}}
            self.assertEqual("tool",payload["messages"][-1]["role"])
            self.assertEqual("56",payload["messages"][-1]["content"])
            return {"message":{"content":"DONE"}}
        result=ready.probe_model("fixture:1",lambda:{"models":[
            {"name":"fixture:1"}]},transport=fake)
        self.assertEqual("MODEL_RESPONDED",result["status"])
        self.assertEqual("OBSERVED",result["tool_calling"])
        self.assertFalse(result["history_saved"])
        self.assertEqual(0,result["external_dispatch_count"])
        self.assertEqual(2,len(seen))
        self.assertFalse(any(m["role"]=="user" and "private" in m["content"]
                             for m in seen[0]["messages"]))

    def test_direct_answer_does_not_count_as_tool_calling(self):
        result=ready.probe_model("fixture:1",lambda:{"models":[
            {"name":"fixture:1"}]},
            lambda payload:{"message":{"content":"56 without tools"}})
        self.assertEqual("NOT_OBSERVED",result["tool_calling"])
        self.assertTrue(result["answer_present"])

    def test_absent_or_invalid_model_rejected_before_inference(self):
        count=[]
        def not_called(payload):
            count.append(payload)
            raise AssertionError("not allowed")
        for model in ("missing:2","","has spaces","x/../../bad",42):
            with self.subTest(model=model),self.assertRaises(agent.AgentError):
                ready.probe_model(model,lambda:{"models":[{"name":"fixture:1"}]},
                                  transport=not_called)
        self.assertEqual([],count)

    def test_denied_tool_call_is_not_a_success(self):
        def fake(_):
            return {"message":{"tool_calls":[{
                "function":{"name":"run_shell","arguments":{}}}]}}
        with self.assertRaisesRegex(agent.AgentError,"tool_not_allowed"):
            ready.probe_model("fixture:1",
                lambda:{"models":[{"name":"fixture:1"}]},transport=fake)


class LocalHTTPTransportTests(unittest.TestCase):
    def make_server(self, status, body, location=None):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(status)
                if location:self.send_header("Location",location)
                self.send_header("Content-Type","application/json")
                self.end_headers()
                self.wfile.write(body)
            def do_POST(self):self.do_GET()
        srv=http.server.ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=srv.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(thread.join,4)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv

    def test_real_http_local_tag_catalog_parse(self):
        doc=json.dumps({"models":[{"name":"fixture:tiny"}]}).encode()
        srv=self.make_server(200,doc)
        old=ready.TAGS_URL
        ready.TAGS_URL="http://127.0.0.1:"+str(srv.server_port)+"/api/tags"
        try:self.assertEqual(["fixture:tiny"],ready.installed_models())
        finally:ready.TAGS_URL=old

    def test_tags_redirect_is_never_followed(self):
        target=self.make_server(200,b'{"models":[{"name":"BAD:1"}]}')
        src=self.make_server(302,b"",location="http://127.0.0.1:"+
                             str(target.server_port)+"/api/tags")
        old=ready.TAGS_URL
        ready.TAGS_URL="http://127.0.0.1:"+str(src.server_port)+"/api/tags"
        try:
            with self.assertRaisesRegex(agent.AgentError,"local_model_unavailable"):
                ready.installed_models()
        finally:ready.TAGS_URL=old

    def test_chat_redirect_is_never_followed(self):
        target=self.make_server(200,b'{"message":{"content":"redirected"}}')
        src=self.make_server(302,b"",location="http://127.0.0.1:"+
                             str(target.server_port)+"/api/chat")
        old=agent.OLLAMA_URL
        agent.OLLAMA_URL="http://127.0.0.1:"+str(src.server_port)+"/api/chat"
        try:
            with self.assertRaisesRegex(agent.AgentError,"local_model_unavailable"):
                agent.ollama_transport({"model":"fake"})
        finally:agent.OLLAMA_URL=old

    def test_oversized_catalog_rejected(self):
        srv=self.make_server(200,b"x"*(ready.MAX_TAG_BYTES+10))
        old=ready.TAGS_URL
        ready.TAGS_URL="http://127.0.0.1:"+str(srv.server_port)+"/api/tags"
        try:
            with self.assertRaisesRegex(agent.AgentError,"model_catalog_too_large"):
                ready.installed_models()
        finally:ready.TAGS_URL=old


if __name__=="__main__":
    unittest.main()
