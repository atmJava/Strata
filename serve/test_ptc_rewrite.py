"""DSH's programmatic mode: a direct call for a tool run_code can reach becomes a run_code call.

DSH's "ptc" preset advertises one tool, run_code, and inside it the same tools are tools.<name>(...).  A model
that writes the template's own <tool_call> for read anyway names a tool the request did not declare; the parser
returns it as text and the user sees the raw XML.  frontend.ptc_wrapped_call moves that call into run_code with
the arguments the model wrote.
"""
import contextlib
import io
import json
import re
import unittest

from serve import frontend
from serve.server import DSH_MAIN_TOOLS, Service, is_dsh_main_request

RUN_CODE = {"name": "run_code", "parameters": {"properties": {"code": {"type": "string"},
                                                              "description": {"type": "string"}}}}
CALL = ("<tool_call>\n<function=read>\n<parameter=file_path>\nD:\\temp\\a.txt\n</parameter>\n"
        "<parameter=offset>\n1\n</parameter>\n</function>\n</tool_call>")


def parse(text, tools=(RUN_CODE,), rewrite=None, width=None):
    parser = frontend.OutputParser(thinking=False, tools=list(tools), stream_tools=True, rewrite=rewrite)
    events = []
    width = width or len(text)
    for i in range(0, len(text), width):
        events += parser.feed(text[i:i + width])
    events += parser.finish()
    return parser, events


def calls_of(events):
    return [e.call for e in events if e.kind == "tool_call"]


class PtcRewriteTests(unittest.TestCase):
    def test_direct_call_becomes_a_run_code_call(self):
        parser, events = parse(CALL, rewrite={"read": "run_code"})
        calls = calls_of(events)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "run_code")
        code = calls[0].arguments["code"]
        self.assertIn('await tools["read"]', code)
        self.assertIn("console.log", code)
        body = re.search(r'await tools\["read"\]\((\{.*?\})\);', code, re.S).group(1)
        self.assertEqual(json.loads(body), {"file_path": "D:\\temp\\a.txt", "offset": 1})
        self.assertEqual(parser.unread, 0)                     # not "a call that went back as text"
        self.assertFalse([e for e in events if e.kind == "content" and "<tool_call>" in e.text])

    def test_the_same_text_without_the_option_is_still_text(self):
        parser, events = parse(CALL)
        self.assertEqual(calls_of(events), [])
        self.assertEqual(parser.unread, 1)
        self.assertIn("<tool_call>", "".join(e.text for e in events if e.kind == "content"))

    def test_a_declared_tool_is_not_rewritten(self):
        tools = (RUN_CODE, {"name": "read", "parameters": {"properties": {"file_path": {"type": "string"},
                                                                         "offset": {"type": "integer"}}}})
        _, events = parse(CALL, tools=tools, rewrite={"read": "run_code"})
        self.assertEqual(calls_of(events)[0].name, "read")

    def test_a_rewritten_call_is_not_announced_while_it_is_written(self):
        # Its parameters are the wrapper's, not the ones the model is writing: streaming them would be wrong.
        parser = frontend.OutputParser(thinking=False, tools=[RUN_CODE], stream_tools=True,
                                       rewrite={"read": "run_code"})
        seen = []
        for i in range(len(CALL)):
            seen += [e.kind for e in parser.feed(CALL[i:i + 1])]
        seen += [e.kind for e in parser.finish()]
        self.assertNotIn("tool_start", seen)
        self.assertNotIn("tool_args", seen)
        self.assertEqual(seen.count("tool_call"), 1)

    def test_rewrite_without_the_wrapper_declared_changes_nothing(self):
        _, events = parse(CALL, tools=(), rewrite={"read": "run_code"})
        self.assertEqual(calls_of(events)[0].name, "read")

    def test_a_rewritten_call_keeps_the_next_call_separate(self):
        _, events = parse(CALL + CALL, rewrite={"read": "run_code"})
        self.assertEqual([c.name for c in calls_of(events)], ["run_code", "run_code"])


class DshDetectionTests(unittest.TestCase):
    def body(self, max_output_tokens):
        return {"model": "m", "stream": True, "max_output_tokens": max_output_tokens,
                "tools": [{"type": "function", "name": name, "parameters": {"type": "object", "properties": {}}}
                          for name in DSH_MAIN_TOOLS],
                "input": [{"role": "developer", "content": "You are a coding agent."},
                          {"role": "user", "content": "Current directory: /tmp"}]}

    def test_the_desktop_profile_max_tokens_counts_as_the_main_shape(self):
        self.assertTrue(is_dsh_main_request(self.body(32768)))   # the profile's maxTokens for the model
        self.assertTrue(is_dsh_main_request(self.body(32000)))   # the older default
        self.assertFalse(is_dsh_main_request(self.body(40000)))


class PrefixImageTests(unittest.TestCase):
    """A picture only rules out the prefix it sits in."""

    def service(self):
        return Service.__new__(Service)

    def test_tokens_spec_is_still_refused_with_any_image(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.service().resolve_prefix({"tokens": 10}, [], [], {}, [1, 2, 3], True), {})
        self.assertIn("ignored: the request has images", out.getvalue())

    def test_messages_spec_is_refused_when_the_picture_is_inside_it(self):
        messages = [{"role": "user", "content": [{"type": "image", "source": "x"}]},
                    {"role": "user", "content": "hello"}]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.service().resolve_prefix({"messages": 1}, messages, [], {}, [1, 2, 3], True), {})
        self.assertIn("have an image", out.getvalue())


if __name__ == "__main__":
    unittest.main()
