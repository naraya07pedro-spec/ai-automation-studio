"""Slice 60: deterministic local Ollama transport failures and timeout contracts."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))

from local_ollama_agent import (AgentError, MAX_RESPONSE_BYTES,
                                ollama_transport)
from local_document_analysis import LocalDocumentError
from local_multi_document_analysis import analyze_multiple_documents
from local_multi_doc_smoke import smoke

DOCUMENTS=[
    {"filename":"policy.txt","content":"Policy review occurs monthly."},
    {"filename":"schedule.md",
     "content":"Maintenance review meeting starts at 18:30 WIB every Thursday."},
]
QUESTION="What time does the maintenance review meeting start?"
QUOTE="Maintenance review meeting starts at 18:30 WIB"


class DummyResponse:
    def __init__(self,raw):self.raw=raw
    def __enter__(self):return self
    def __exit__(self,*_):return False
    def read(self,_limit):return self.raw


class DummyOpener:
    def __init__(self,result):
        self.result=result
        self.calls=[]
    def open(self,request,timeout):
        self.calls.append((request,timeout))
        if isinstance(self.result,Exception):raise self.result
        return DummyResponse(self.result)


class ModelTransportDiagnosticTests(unittest.TestCase):
    def test_bounded_timeout_is_explicit_and_uses_fixed_local_endpoint(self):
        opener=DummyOpener(b'{"message":{"content":"ok"}}')
        response=ollama_transport({"model":"fixture:model"},opener=opener,
                                  timeout_seconds=90)
        self.assertEqual("ok",response["message"]["content"])
        self.assertEqual(1,len(opener.calls))
        request,timeout=opener.calls[0]
        self.assertEqual(90,timeout)
        self.assertEqual("http://127.0.0.1:11434/api/chat",request.full_url)
        self.assertEqual("POST",request.get_method())
        self.assertNotIn("cloud",request.full_url)
        for invalid in (0,4,91,120,True,"90",90.0,None):
            with self.subTest(value=invalid),self.assertRaisesRegex(
                 AgentError,"invalid_local_model_timeout"):
                ollama_transport({},opener=opener,timeout_seconds=invalid)
        self.assertEqual(1,len(opener.calls))

    def test_timeout_and_http_failures_are_distinguished_with_one_call(self):
        url="http://127.0.0.1:11434/api/chat"
        cases=[
            (TimeoutError("timed out"),"local_model_timeout"),
            (URLError(TimeoutError("timed out")),"local_model_timeout"),
            (URLError(ConnectionRefusedError("connection refused")),"local_model_unavailable"),
            (ConnectionRefusedError("connection refused"),"local_model_unavailable"),
            (URLError(ConnectionResetError("connection reset")),"local_model_transport_uncertain"),
            (ConnectionResetError("connection reset"),"local_model_transport_uncertain"),
            (URLError(BrokenPipeError("broken pipe")),"local_model_transport_uncertain"),
            (BrokenPipeError("broken pipe"),"local_model_transport_uncertain"),
            (URLError(OSError("unknown transport phase")),"local_model_transport_uncertain"),
            (OSError("unknown transport phase"),"local_model_transport_uncertain"),
            (HTTPError(url,408,"Request Timeout",{},None),"local_model_timeout"),
            (HTTPError(url,504,"Gateway Timeout",{},None),"local_model_timeout"),
            (HTTPError(url,404,"Not Found",{},None),"local_model_endpoint_missing"),
            (HTTPError(url,500,"Internal Error",{},None),"local_model_server_error"),
            (HTTPError(url,503,"Unavailable",{},None),"local_model_server_error"),
            (HTTPError(url,302,"Redirect",{"Location":"http://evil.invalid"},None),
             "local_model_unavailable"),
        ]
        for error,expected in cases:
            with self.subTest(expected=expected,error=repr(error)):
                opener=DummyOpener(error)
                with self.assertRaisesRegex(AgentError,"^"+expected+"$"):
                    ollama_transport({"model":"fixture:model"},opener=opener)
                self.assertEqual(1,len(opener.calls),"Must never retry a POST")

    def test_definite_connect_refusal_with_errno_does_not_latch_as_uncertain(self):
        import errno
        for failure in (OSError(errno.ECONNREFUSED, "Connection refused"),
                        URLError(OSError(errno.ECONNREFUSED, "Connection refused"))):
            with self.subTest(failure=repr(failure)):
                with self.assertRaisesRegex(AgentError,"^local_model_unavailable$"):
                    ollama_transport({"model":"fixture:model"},
                                     opener=DummyOpener(failure))

    def test_malformed_or_oversize_response_still_fails_closed(self):
        for raw,expected in [(b"not json","invalid_model_response"),
                             (b"x"*(MAX_RESPONSE_BYTES+1),
                              "model_response_too_large")]:
            with self.subTest(expected=expected),self.assertRaisesRegex(
                 AgentError,expected):
                ollama_transport({},opener=DummyOpener(raw))

    def test_analysis_exposes_stable_diagnostic_without_sensitive_message(self):
        for code in ("local_model_timeout","local_model_transport_uncertain",
                     "local_model_server_error",
                     "local_model_endpoint_missing","local_model_unavailable",
                     "invalid_model_response","model_response_too_large"):
            with self.subTest(code=code),self.assertRaisesRegex(
                 LocalDocumentError,"^"+code+"$"):
                analyze_multiple_documents(
                    DOCUMENTS,QUESTION,"fixture:model",
                    transport=lambda _,code=code:(_ for _ in ()).throw(
                        AgentError(code)))
        with self.assertRaisesRegex(
             LocalDocumentError,"local_multi_document_model_unavailable"):
            analyze_multiple_documents(
                DOCUMENTS,QUESTION,"fixture:model",
                transport=lambda _:(_ for _ in ()).throw(
                    AgentError("untrusted_internal_error:secret")))

    def test_real_transport_acceptance_uses_90_second_budget_not_25(self):
        calls=[]
        def fake_transport(payload,*,timeout_seconds=25):
            calls.append((payload,timeout_seconds))
            return {"message":{"content":json.dumps({
                "answer":"The maintenance review starts at 18:30 WIB.",
                "insufficient_evidence":False,
                "citations":[{"chunk_id":"F2C1","quote":QUOTE}]
            })}}
        with patch("local_multi_doc_smoke.ollama_transport",fake_transport):
            # Injected model catalog means this is still TEST evidence,
            # even if the transport argument is omitted.
            report=smoke("fixture:model",catalog=lambda:["fixture:model"],
                         memory=lambda:None)
        self.assertEqual(1,len(calls))
        self.assertEqual(90,calls[0][1])
        self.assertEqual("SYNTHETIC_MODEL_SMOKE_PASSED",report["status"])
        self.assertEqual("INJECTED_TEST_TRANSPORT",report["inference_source"])
        self.assertIsNone(report["model_request_timeout_seconds"])
        self.assertEqual(0,report["external_write_count"])


if __name__=="__main__":
    unittest.main()
