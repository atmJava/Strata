import contextlib
import io
import json
import pathlib

from strata_cli.server_cli import main


def catalog_with_template():
    return {
        "models": [
            {
                "slug": "deepseek-flash",
                "display_name": "DeepSeek-Flash",
                "apply_patch_tool_type": "freeform",
                "web_search_tool_type": "text",
                "context_window": 1048576,
                "max_context_window": 1048576,
                "auto_compact_token_limit": None,
                "priority": 1,
                "availability_nux": "shown",
                "model_messages": {"instructions_template": "agent"},
            }
        ]
    }


def run_cli(*args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(list(args))
    return code, output.getvalue()


def test_cli_checks_before_writing_and_applies_idempotently(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(catalog_with_template()), encoding="utf-8")

    code, output = run_cli(
        "codex",
        "catalog",
        "--catalog",
        str(path),
        "--json",
    )
    assert code == 0
    assert json.loads(output)["action"] == "added"
    assert json.loads(output)["applied"] is False
    assert len(json.loads(path.read_text(encoding="utf-8"))["models"]) == 1

    code, output = run_cli(
        "codex",
        "catalog",
        "--catalog",
        str(path),
        "--apply",
        "--output",
        "json",
    )
    result = json.loads(output)
    assert code == 0
    assert result["action"] == "added"
    assert result["applied"] is True
    assert pathlib.Path(result["backup"]).is_file()
    applied = json.loads(path.read_text(encoding="utf-8"))
    assert applied["models"][-1]["slug"] == (
        "qwen3.8-flash-next-iq3_xxs-abliterated"
    )
    assert applied["models"][-1]["apply_patch_tool_type"] == "freeform"

    code, output = run_cli(
        "codex",
        "catalog",
        "--catalog",
        str(path),
        "--apply",
        "--output",
        "json",
    )
    second = json.loads(output)
    assert code == 0
    assert second["action"] == "unchanged"
    assert second["applied"] is False
    assert second["backup"] is None


def test_cli_rejects_a_template_without_apply_patch(tmp_path):
    path = tmp_path / "models.json"
    catalog = catalog_with_template()
    del catalog["models"][0]["apply_patch_tool_type"]
    path.write_text(json.dumps(catalog), encoding="utf-8")

    code, output = run_cli(
        "codex",
        "catalog",
        "--catalog",
        str(path),
        "--json",
    )
    assert code == 2
    assert json.loads(output)["ok"] is False
    assert "freeform apply_patch" in json.loads(output)["error"]["message"]
