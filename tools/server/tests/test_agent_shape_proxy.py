import importlib.util
import json
import os
import pathlib
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "agent-shape-proxy.py"
SPEC = importlib.util.spec_from_file_location("agent_shape_proxy", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def request(items, *, tools=None, kind="turn"):
    turn = {
        "session_id": "session-secret",
        "thread_id": "thread-secret",
        "request_kind": kind,
    }
    return {
        "model": "qwen",
        "stream": True,
        "instructions": "instructions-secret",
        "tools": tools if tools is not None else [{"name": "exec_command"}],
        "client_metadata": {"x-codex-turn-metadata": json.dumps(turn)},
        "input": items,
    }


def message(text):
    return {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": text}],
    }


def output(call_id, text):
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": text,
    }


class AgentShapeProxyTest(unittest.TestCase):
    def test_forward_headers_replaces_case_insensitive_content_length(self):
        headers = MODULE.forward_headers(
            {
                "content-length": "999",
                "Content-Length": "888",
                "Host": "local",
                "Connection": "keep-alive",
                "Authorization": "Bearer secret",
            },
            42,
        )
        self.assertEqual(headers["Content-Length"], "42")
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertEqual(
            [key.lower() for key in headers].count("content-length"), 1
        )
        self.assertNotIn("Host", headers)
        self.assertNotIn("Connection", headers)

    def test_shape_contains_hashes_not_bodies_or_raw_ids(self):
        shaped = MODULE.request_shape(request([message("private prompt")]))
        rendered = json.dumps(shaped)
        self.assertNotIn("private prompt", rendered)
        self.assertNotIn("instructions-secret", rendered)
        self.assertNotIn("session-secret", rendered)
        self.assertNotIn("thread-secret", rendered)
        self.assertEqual(shaped["input"]["count"], 1)
        self.assertEqual(shaped["tools"]["names"], ["exec_command"])

    def test_compaction_tools_are_the_first_divergence(self):
        previous = MODULE.request_shape(request([message("same")]))
        current = MODULE.request_shape(request([message("same")], tools=[], kind="compaction"))
        difference = MODULE.first_divergence(previous, current)
        self.assertEqual(difference["location"], "tools")
        self.assertEqual(difference["previous_count"], 1)
        self.assertEqual(difference["current_count"], 0)

    def test_matching_tools_show_an_input_append(self):
        ordinary = request([message("same")])
        ordinary["instructions"] = None
        compact = request([message("same"), message("compact")], kind="compaction")
        compact["instructions"] = None
        previous = MODULE.request_shape(ordinary)
        current = MODULE.request_shape(compact)
        difference = MODULE.first_divergence(previous, current)
        self.assertEqual(difference["location"], "input_append")
        self.assertEqual(difference["common_items"], 1)

    def test_changed_history_item_reports_no_prefix_body(self):
        previous = MODULE.request_shape(request([message("before")]))
        current = MODULE.request_shape(request([message("after")], kind="compaction"))
        difference = MODULE.first_divergence(previous, current)
        self.assertEqual(difference["location"], "input_item")
        self.assertEqual(difference["item_index"], 0)
        self.assertNotIn("before", json.dumps(difference))
        self.assertNotIn("after", json.dumps(difference))

    def test_response_summary_keeps_usage_only(self):
        event = {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "usage": {
                    "input_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 75},
                    "output_tokens": 5,
                },
                "output": [{"content": [{"text": "private answer"}]}],
            },
        }
        raw = f"data: {json.dumps(event)}\n\n".encode()
        summary = MODULE.response_summary(raw, "text/event-stream")
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["usage"]["cached_tokens"], 75)
        self.assertNotIn("private answer", json.dumps(summary))

    def test_recorder_pairs_compaction_with_prior_ordinary_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "shapes.jsonl"
            recorder = MODULE.ShapeRecorder(path)
            ordinary = MODULE.request_shape(request([message("same")]))
            compaction = MODULE.request_shape(
                request([message("same"), message("compact")], kind="compaction")
            )
            recorder.record(ordinary)
            result = recorder.record(compaction)
            self.assertEqual(result["comparison"]["location"], "input_append")
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(rows), 2)

    def test_context_diet_keeps_recent_and_error_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            diet = MODULE.ContextDiet(pathlib.Path(tmp) / "artifacts")
            items = [message("start")]
            items.append(output("old", "old-success-" + "x" * 5000))
            items.append(output("bad", "Error: test failed\n" + "y" * 5000))
            for index in range(6):
                items.append(output(f"recent-{index}", f"recent-{index}"))
            result, summary = diet.transform(request(items))
            self.assertEqual(summary["replaced"], 1)
            self.assertGreater(summary["saved_chars"], 0)
            self.assertIn("Strata context diet", result["input"][1]["output"])
            self.assertTrue(result["input"][2]["output"].startswith("Error:"))
            self.assertEqual(result["input"][-1]["output"], "recent-5")

    def test_context_diet_artifact_round_trip_and_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            diet = MODULE.ContextDiet(pathlib.Path(tmp) / "artifacts")
            original = "full output\n" + "z" * 5000
            result, summary = diet.transform(
                request([message("start"), output("call", original)])
            )
            self.assertEqual(summary["replaced"], 0)
            items = [message("start")]
            items.append(output("call", original))
            for index in range(6):
                items.append(output(f"recent-{index}", f"recent-{index}"))
            result, summary = diet.transform(request(items))
            self.assertEqual(summary["replaced"], 1)
            reference = result["input"][1]["output"]
            path = next(
                pathlib.Path(line.split("path=", 1)[1])
                for line in reference.splitlines()
                if line.startswith("path=")
            )
            self.assertEqual(path.read_text(), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_context_diet_reuses_same_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            diet = MODULE.ContextDiet(pathlib.Path(tmp) / "artifacts")
            items = [message("start"), output("call", "a" * 5000)]
            items.extend(output(f"recent-{index}", "small") for index in range(6))
            first, _ = diet.transform(request(items))
            second, _ = diet.transform(request(items))
            self.assertEqual(first["input"][1]["output"], second["input"][1]["output"])

    def test_eager_context_diet_keeps_transformed_prefix_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            diet = MODULE.ContextDiet(
                pathlib.Path(tmp) / "artifacts", keep_recent=0
            )
            first_items = [message("start"), output("first", "a" * 5000)]
            first, first_summary = diet.transform(request(first_items))
            second_items = first_items + [output("second", "b" * 5000)]
            second, second_summary = diet.transform(request(second_items))
            self.assertEqual(first_summary["replaced"], 1)
            self.assertEqual(second_summary["replaced"], 2)
            self.assertEqual(first["input"][1], second["input"][1])

    def test_context_diet_rejects_symlink_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "target"
            target.mkdir()
            link = pathlib.Path(tmp) / "link"
            os.symlink(target, link)
            with self.assertRaises(ValueError):
                MODULE.ContextDiet(link)

    def test_context_diet_noop_without_conversation_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            diet = MODULE.ContextDiet(pathlib.Path(tmp) / "artifacts")
            req = request([message("start"), output("call", "a" * 5000)])
            req["client_metadata"] = None
            result, summary = diet.transform(req)
            self.assertIs(result, req)
            self.assertEqual(summary["replaced"], 0)


if __name__ == "__main__":
    unittest.main()
