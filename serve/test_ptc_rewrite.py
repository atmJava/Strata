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
from serve import responses as responses_api
from serve.server import DSH_MAIN_TOOLS, Service, is_dsh_main_request, ptc_rewrite_map

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


class PrefixSpellings(unittest.TestCase):
    """The model writes the programmatic prompt's own spelling: `tools.edit`, `tools edit` and the bare `tools`."""

    def body(self, name):
        return ("<tool_call>\n<function=" + name + ">\n<parameter=file_path>\na.txt\n</parameter>\n"
                "</function>\n</tool_call>")

    def test_the_dotted_spelling_is_the_tool(self):
        _, events = parse(self.body("tools.edit"), rewrite={"read": "run_code", "edit": "run_code"})
        calls = calls_of(events)
        self.assertEqual([c.name for c in calls], ["run_code"])
        self.assertIn('"edit"', calls[0].arguments["code"])
        self.assertIn("a.txt", calls[0].arguments["code"])

    def test_the_space_spelling_is_the_tool(self):
        _, events = parse(self.body("tools edit"), rewrite={"read": "run_code", "edit": "run_code"})
        self.assertEqual([c.name for c in calls_of(events)], ["run_code"])

    def test_the_bracket_spelling_is_the_tool(self):
        # Seen live: <function=tools["read"]> — the JavaScript property access the model writes in a run_code program.
        for name in ('tools["read"]', "tools['read']", "tools[read]", 'tools["read"]()', 'tools [ "read" ]',
                     'tools[`read`]'):
            _, events = parse(self.body(name), rewrite={"read": "run_code"})
            calls = calls_of(events)
            self.assertEqual([c.name for c in calls], ["run_code"], name)
            self.assertIn('"read"', calls[0].arguments["code"], name)
            self.assertIn("a.txt", calls[0].arguments["code"], name)

    def test_the_bracket_spelling_streams_whole(self):
        # It arrived streamed; every cut must give the same single run_code call and no XML in the answer.
        for width in (1, 2, 7, 13):
            _, events = parse(self.body('tools["read"]'), rewrite={"read": "run_code"}, width=width)
            self.assertEqual([c.name for c in calls_of(events)], ["run_code"], width)
            self.assertFalse([e for e in events if e.kind == "content" and "tool_call" in (e.text or "")], width)

    def test_the_bracket_spelling_of_the_wrapper_itself(self):
        _, events = parse(self.body('tools["run_code"]'), rewrite={"read": "run_code"})
        calls = calls_of(events)
        self.assertEqual([c.name for c in calls], ["run_code"])
        self.assertEqual({k: v for k, v in calls[0].arguments.items() if k != "description"},
                         {"file_path": "a.txt"})

    def test_the_dotted_spelling_of_the_wrapper_itself_is_not_wrapped_twice(self):
        _, events = parse(self.body("tools.run_code"), rewrite={"read": "run_code"})
        calls = calls_of(events)
        self.assertEqual([c.name for c in calls], ["run_code"])
        self.assertEqual({k: v for k, v in calls[0].arguments.items() if k != "description"},
                         {"file_path": "a.txt"})    # its own arguments, not a wrapper


class AnyUndeclaredName(unittest.TestCase):
    """The sentinel map: with only the wrapper declared, every undeclared name is the wrapper's call."""

    def body(self, name):
        return ("<tool_call>\n<function=" + name + ">\n<parameter=file_path>\na.txt\n</parameter>\n"
                "</function>\n</tool_call>")

    def test_an_unknown_name_is_delivered_as_the_wrapper(self):
        _, events = parse(self.body("some_tool_this_build_does_not_know"), rewrite={"*": "run_code"})
        calls = calls_of(events)
        self.assertEqual([c.name for c in calls], ["run_code"])
        self.assertIn('"some_tool_this_build_does_not_know"', calls[0].arguments["code"])

    def test_a_declared_tool_is_still_called_as_declared(self):
        _, events = parse(self.body("run_code"), rewrite={"*": "run_code"})
        call = calls_of(events)[0]
        self.assertEqual({k: v for k, v in call.arguments.items() if k != "description"},
                         {"file_path": "a.txt"})


class WrapperArguments(unittest.TestCase):
    def test_a_missing_description_is_filled(self):
        _, events = parse('<tool_call>\n<function=run_code>\n<parameter=code>\nconst x = 1;\n</parameter>\n'
                          '</function>\n</tool_call>')
        self.assertIn("description", calls_of(events)[0].arguments)

    def test_a_written_description_is_kept(self):
        _, events = parse('<tool_call>\n<function=run_code>\n<parameter=code>\nconst x = 1;\n</parameter>\n'
                          '<parameter=description>\nmine\n</parameter>\n</function>\n</tool_call>')
        self.assertEqual(calls_of(events)[0].arguments["description"], "mine")


class PromptToolNames(unittest.TestCase):
    """The request declares only run_code; the other names live in its developer text, so that is what is read."""

    def test_names_come_from_the_developer_text(self):
        req = {"input": [
            {"role": "developer", "content": [
                {"type": "input_text", "text": 'Call tools.ask_user_question(args), tools["present"]({}) or '
                                                "tools['schedule_list']()."}]},
            {"role": "user", "content": [{"type": "input_text", "text": "tools.not_a_tool"}]}]}
        self.assertEqual(responses_api.ptc_tool_names(req),
                         {"ask_user_question", "present", "schedule_list"})

    def test_instructions_count_too(self):
        self.assertEqual(responses_api.ptc_tool_names({"instructions": "tools.present({...})"}), {"present"})

    def test_the_map_takes_any_undeclared_name(self):
        # "*" is the frontend's sentinel: the wrapper takes every name the request did not declare, because a profile
        # or an MCP server may offer tools this build's list does not know.
        self.assertEqual(ptc_rewrite_map({"run_code", "read"}), {"*": "run_code"})
        self.assertEqual(ptc_rewrite_map({"read"}), {})

    def test_a_prompt_named_tool_is_rewritten(self):
        _, events = parse('<tool_call>\n<function=ask_user_question>\n<parameter=question>\nq\n</parameter>\n'
                          '</function>\n</tool_call>',
                          rewrite=ptc_rewrite_map({"run_code"}, {"ask_user_question"}))
        self.assertEqual([c.name for c in calls_of(events)], ["run_code"])


class DshDetectionTests(unittest.TestCase):
    def body(self, max_output_tokens):
        return {"model": "m", "stream": True, "max_output_tokens": max_output_tokens,
                "tools": [{"type": "function", "name": name, "parameters": {"type": "object", "properties": {}}}
                          for name in DSH_MAIN_TOOLS],
                "input": [{"role": "developer", "content": "You are a coding agent."},
                          {"role": "user", "content": "Current directory: /tmp"}]}

    def test_the_preset_and_the_profile_do_not_matter(self):
        # 32768, 32000 and 40000 are all DSH: the shape is the developer message and tools, not a token count.
        for tokens in (32768, 32000, 40000, 65536):
            self.assertTrue(is_dsh_main_request(self.body(tokens)), tokens)

    def test_the_programmatic_preset_is_the_main_shape(self):
        req = self.body(40000)
        req["tools"] = [{"type": "function", "name": "run_code", "parameters": {"type": "object", "properties": {}}}]
        self.assertTrue(is_dsh_main_request(req))

    def test_a_compaction_is_not(self):
        req = self.body(40000)
        req["tools"] = []
        self.assertFalse(is_dsh_main_request(req))
        req = self.body(40000)
        req.pop("tools")
        self.assertFalse(is_dsh_main_request(req))


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
