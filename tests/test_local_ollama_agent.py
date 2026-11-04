"""No external model, network, secrets, or filesystem required."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from local_ollama_agent import run_agent, execute_tool, calculate, AgentError, MAX_TURNS


class AgentTests(unittest.TestCase):
    def test_real_model_protocol_plain_answer(self):
        seen = []
        def fake(payload):
            seen.append(payload)
            return {"message": {"role": "assistant", "content": "Jawaban lokal"}}
        result = run_agent("halo", "qwen2.5:3b", fake)
        self.assertEqual("Jawaban lokal", result["answer"])
        self.assertEqual("LOCAL_MODEL", result["mode"])
        self.assertEqual(0, result["external_dispatch_count"])
        self.assertEqual([], result["tools_used"])
        self.assertEqual(False, seen[0]["stream"])
        self.assertEqual("qwen2.5:3b", seen[0]["model"])

    def test_model_calls_tool_then_answers(self):
        calls = []
        def fake(payload):
            calls.append(payload)
            if len(calls) == 1:
                return {"message": {"content": "", "tool_calls": [
                    {"function": {"name": "calculate",
                                  "arguments": {"a": "3", "b": "7", "operation": "multiply"}}}]}}
            self.assertEqual("21", payload["messages"][-1]["content"])
            return {"message": {"content": "Hasilnya 21."}}
        result = run_agent("3 * 7?", "llama3.2", fake)
        self.assertEqual(["calculate"], result["tools_used"])
        self.assertEqual("Hasilnya 21.", result["answer"])

    def test_two_tools_in_one_round(self):
        def fake(payload):
            if len(payload["messages"]) == 2:
                return {"message": {"tool_calls": [
                    {"function": {"name": "calculate", "arguments":
                     {"a": "1", "b": "2", "operation": "add"}}},
                    {"function": {"name": "utc_now", "arguments": {}}}]}}
            return {"message": {"content": "done"}}
        self.assertEqual(["calculate", "utc_now"], run_agent("check", "m", fake)["tools_used"])

    def test_calculator_is_bounded(self):
        self.assertEqual("2.5", calculate({"a": "1.0", "b": "1.5", "operation": "add"}))
        for args in ({"a": "1", "b": "0", "operation": "divide"},
                     {"a": "NaN", "b": "1", "operation": "add"},
                     {"a": "1e99", "b": "1", "operation": "add"},
                     {"a": "1", "b": "2", "operation": "exec"},
                     {"a": 1, "b": "2", "operation": "add"},
                     {"a": "1", "b": "2", "operation": "add", "secret": "bad"}):
            with self.subTest(args=args), self.assertRaises(AgentError):
                calculate(args)

    def test_disallowed_tool_never_executes(self):
        def fake(_):
            return {"message": {"tool_calls": [
                {"function": {"name": "send_email",
                              "arguments": {"to": "somewhere"}}}]}}
        with self.assertRaisesRegex(AgentError, "tool_not_allowed"):
            run_agent("send", "m", fake)
        with self.assertRaisesRegex(AgentError, "tool_not_allowed"):
            execute_tool("run_shell", {})

    def test_step_limit(self):
        def fake(_):
            return {"message": {"tool_calls": [
                {"function": {"name": "utc_now", "arguments": {}}}]}}
        with self.assertRaisesRegex(AgentError, "agent_step_limit"):
            run_agent("repeat", "m", fake)

    def test_bad_model_and_prompt_rejected_pre_transport(self):
        def fail(_):
            raise AssertionError("transport must never run")
        for model in ("", "x/../../evil", "bad name", "x" * 81):
            with self.subTest(model=model), self.assertRaises(AgentError):
                run_agent("hello", model, fail)
        for prompt in ("", " " * 5, "a" * 3001):
            with self.subTest(prompt=prompt), self.assertRaises(AgentError):
                run_agent(prompt, "m", fail)

    def test_malformed_model_responses(self):
        for response in ({}, {"message": []}, {"message": {"content": ""}},
                         {"message": {"content": 1}},
                         {"message": {"content": "x" * 12001}},
                         {"message": {"tool_calls": "bad"}},
                         {"message": {"tool_calls": [{}, {}, {}]}}):
            with self.subTest(response=str(response)[:30]), self.assertRaises(AgentError):
                run_agent("hello", "m", lambda _: response)

    def test_bad_tool_arguments_rejected(self):
        def fake(_):
            return {"message": {"tool_calls": [
                {"function": {"name": "calculate", "arguments": "1+1"}}]}}
        with self.assertRaisesRegex(AgentError, "invalid_tool_calls"):
            run_agent("calc", "m", fake)


if __name__ == "__main__":
    unittest.main()
