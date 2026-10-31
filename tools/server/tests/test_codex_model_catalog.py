import importlib.util
import json
import pathlib
import sys


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "codex-model-catalog.py"
SPEC = importlib.util.spec_from_file_location("codex_model_catalog", SCRIPT)
CATALOG = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = CATALOG
SPEC.loader.exec_module(CATALOG)


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


def update(catalog):
    return CATALOG.update_catalog(
        catalog,
        template_slug="deepseek-flash",
        slug="qwen3.8-flash-next-iq3_xxs-abliterated",
        display_name="Qwen3.8-Flash-Next IQ3_XXS",
        context_window=262144,
        auto_compact_token_limit=210000,
    )


def test_adds_apply_patch_metadata_and_is_idempotent():
    updated, action = update(catalog_with_template())
    assert action == "added"
    entry = updated["models"][-1]
    assert entry["slug"] == "qwen3.8-flash-next-iq3_xxs-abliterated"
    assert entry["apply_patch_tool_type"] == "freeform"
    assert entry["context_window"] == 262144
    assert entry["auto_compact_token_limit"] == 210000
    same, action = update(updated)
    assert action == "unchanged"
    assert same == updated


def test_rejects_missing_template_and_bad_context():
    catalog = catalog_with_template()
    try:
        CATALOG.update_catalog(
            catalog,
            template_slug="missing",
            slug="qwen",
            display_name="Qwen",
            context_window=100,
            auto_compact_token_limit=50,
        )
    except ValueError as exc:
        assert "template" in str(exc)
    else:
        raise AssertionError("missing template was accepted")
    try:
        update(catalog)
        CATALOG.update_catalog(
            catalog,
            template_slug="deepseek-flash",
            slug="qwen",
            display_name="Qwen",
            context_window=100,
            auto_compact_token_limit=100,
        )
    except ValueError as exc:
        assert "below" in str(exc)
    else:
        raise AssertionError("bad context was accepted")


def test_write_catalog_keeps_a_backup(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(catalog_with_template()), encoding="utf-8")
    updated, _ = update(json.loads(path.read_text()))
    backup = CATALOG.write_catalog(path, updated)
    assert backup.is_file()
    assert json.loads(path.read_text()) == updated
    assert json.loads(backup.read_text()) == catalog_with_template()
