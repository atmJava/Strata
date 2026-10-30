"""The client SSE probe must measure the wire and join it to engine token sources."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from serve.frontend import ChatTemplate
from serve.server import ByteTokenizer, Service, serve
from serve.test_server import ThinkingEngine


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools" / "server" / "probe-client-sse.py"
spec = importlib.util.spec_from_file_location("probe_client_sse", SCRIPT)
probe = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(probe)


class ClientSseProbe(unittest.TestCase):
    def setUp(self):
        tokenizer = ByteTokenizer()
        engine = ThinkingEngine(tokenizer)
        service = Service(engine, tokenizer, ChatTemplate(Path(ROOT / "serve" / "chat_template.jinja")))
        self.httpd = serve(service, port=0)
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def run_api(self, api: str) -> dict:
        body = probe.request_body(api, "m", "2+2?", 400, None)
        return probe.run_probe(self.base_url, api, body, "", timeout_s=10.0, settle_s=2.0)

    def assert_joined(self, report: dict) -> None:
        join = report["server_join"]
        self.assertTrue(join["available"], join)
        self.assertGreater(join["client_visible_delta_events"], 0, report)
        self.assertEqual(join["client_visible_delta_events"], join["server_visible_delta_events"], report)
        self.assertEqual(join["matched_delta_events"], join["client_visible_delta_events"], report)
        self.assertEqual(join["missing_engine_source"], [], report)
        self.assertTrue(join["exact_when_retained"], report)
        self.assertIsNotNone(join["first_mapped_source"], report)
        self.assertIsNotNone(join["last_mapped_source"], report)
        self.assertLessEqual(join["first_client_delta_ms"], join["last_client_delta_ms"])
        self.assertFalse(report["client"]["client_rendered"])
        self.assertFalse(report["client"]["client_ack"]["available"])

    def test_openai_stream_joins_client_deltas_to_engine_tokens(self):
        report = self.run_api("chat")
        self.assertTrue(report["client"]["done_frame_seen"], report)
        self.assert_joined(report)

    def test_responses_stream_joins_client_deltas_to_engine_tokens(self):
        self.assert_joined(self.run_api("responses"))

    def test_anthropic_stream_joins_client_deltas_to_engine_tokens(self):
        self.assert_joined(self.run_api("anthropic"))

    def test_visible_chars_covers_all_three_protocols(self):
        self.assertEqual(probe.visible_chars("chat", {"choices": [{"delta": {"content": "ab"}}]}), 2)
        self.assertEqual(probe.visible_chars(
            "responses", {"type": "response.output_text.delta", "delta": "abc"}), 3)
        self.assertEqual(probe.visible_chars(
            "anthropic", {"type": "content_block_delta", "delta": {"text": "abcd"}}), 4)
        self.assertEqual(probe.visible_chars("chat", {"choices": [{"delta": {}}]}), 0)


if __name__ == "__main__":
    unittest.main()
