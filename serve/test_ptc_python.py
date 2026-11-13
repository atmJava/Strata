"""The programmatic wrapper: which language DSH declared, and what the rewritten call must be.

DSH ships two run_code flavours and announces the one it will run in the request text; a program written in
the other language is a syntax error at the client, so the wrapper has to match what was declared."""
import ast
import unittest

from serve import frontend
from serve.server import ptc_prompt_texts

RUN_CODE = {"name": "run_code", "description": "the programmatic wrapper", "parameters":
            {"type": "object", "properties": {"code": {"type": "string"}, "description": {"type": "string"}},
             "required": ["code", "description"]}}
CALL = ("<tool_call>\n<function=read>\n<parameter=file_path>\n/tmp/a.txt\n</parameter>\n"
        "</function>\n</tool_call>")
TS_SDK = "Program-only SDK bindings:\ninterface ToolArgsMap {\n  read: { file_path: string };\n}"
PY_SDK = "Program-only SDK bindings:\nclass ReadArgs(TypedDict):\n    file_path: str\nasync def main():\n    pass"


def parse(text, tools=(RUN_CODE,), rewrite=None, python=False):
    p = frontend.OutputParser(thinking=False, tools=list(tools), stream_tools=True,
                              rewrite=rewrite, ptc_python=python)
    return p, [e.call for e in (list(p.feed(text)) + list(p.finish("stop"))) if e.kind == "tool_call"]


class ProgramLanguage(unittest.TestCase):
    def test_the_typescript_projection_is_the_default(self):
        _, calls = parse(CALL, rewrite={"read": "run_code"})
        self.assertTrue(calls[0].arguments["code"].startswith("const r = await tools["))
        self.assertIn("console.log(", calls[0].arguments["code"])

    def test_the_python_projection_gets_a_python_program(self):
        _, calls = parse(CALL, rewrite={"read": "run_code"}, python=True)
        code = calls[0].arguments["code"]
        self.assertTrue(code.startswith("import json"), code[:40])
        self.assertIn("json.loads(", code)
        self.assertIn("print(", code)
        ast.parse("async def _f(tools):\n" + "".join("    " + l + "\n" for l in code.split("\n")))

    def test_the_arguments_survive_as_json(self):
        p, calls = parse(CALL, rewrite={"read": "run_code"}, python=True)
        self.assertIn("file_path", calls[0].arguments["code"])
        self.assertIn("/tmp/a.txt", calls[0].arguments["code"])

    def test_a_rewritten_call_is_not_an_unread_call(self):
        # unread means the block reached the client as text; a rewritten one did not
        p, calls = parse(CALL, rewrite={"*": "run_code"})
        self.assertEqual(p.rewritten, 1)
        self.assertEqual(p.unread, 0)
        self.assertEqual(p.unread_names, [])

    def test_the_description_is_short_and_active(self):
        _, calls = parse(CALL, rewrite={"read": "run_code"})
        desc = calls[0].arguments["description"]
        self.assertLessEqual(len(desc.split()), 10, desc)
        self.assertIn("read", desc)


class PromptShape(unittest.TestCase):
    def test_the_chat_shape_carries_the_block(self):
        req = {"messages": [{"role": "system", "content": PY_SDK}, {"role": "user", "content": "hi"}]}
        self.assertTrue(frontend.ptc_program_is_python(ptc_prompt_texts(req)))

    def test_the_responses_shape_carries_the_block(self):
        req = {"instructions": PY_SDK, "input": [{"role": "user", "content": "hi"}]}
        self.assertTrue(frontend.ptc_program_is_python(ptc_prompt_texts(req)))

    def test_the_typescript_block_is_not_taken_for_python(self):
        req = {"messages": [{"role": "system", "content": TS_SDK}]}
        self.assertFalse(frontend.ptc_program_is_python(ptc_prompt_texts(req)))

    def test_a_plain_request_declares_neither(self):
        self.assertFalse(frontend.ptc_program_is_python(ptc_prompt_texts(
            {"messages": [{"role": "user", "content": "hi"}]})))


if __name__ == "__main__":
    unittest.main()
