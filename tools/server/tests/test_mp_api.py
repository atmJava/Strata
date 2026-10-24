"""T1 - API contract (docs/FEATURE_PARITY.md section 4).

These are the client-visible behaviours the fork's tests/entrypoints/openai suite
covers, reduced to what can be checked over HTTP against Strata's server.
"""
import urllib.error

import pytest

import mp_client as mp


def test_health(server):
    assert isinstance(server, dict)


def test_models(server):
    models = mp.get_json("/v1/models")
    ids = [entry.get("id") for entry in models.get("data", [])]
    assert ids, f"no model ids in {models}"


def test_chat_nonstream_usage(server):
    reply = mp.post_json("/v1/chat/completions", mp.chat_body("Reply with exactly: OK", max_tokens=16))
    choice = reply["choices"][0]
    assert (choice["message"].get("content") or "").strip()
    usage = reply["usage"]
    assert usage["prompt_tokens"] > 0
    assert usage["completion_tokens"] > 0
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
    assert choice["finish_reason"] in ("stop", "length")


def test_chat_stream_usage(server):
    body = mp.chat_body("Count from 1 to 10, comma separated.", max_tokens=32, stream=True)
    text, usage, first = mp.collect_stream(body)
    assert text.strip()
    assert first is not None
    assert usage and usage["completion_tokens"] > 0


def test_bad_request_400(server):
    body = {"model": mp.MODEL, "messages": [], "max_tokens": 8}
    with pytest.raises(urllib.error.HTTPError) as caught:
        mp.post_json("/v1/chat/completions", body)
    assert caught.value.code == 400


def test_missing_key_401(server):
    if not mp.API_KEY:
        pytest.skip("server has no api key; this check is moot")
    with pytest.raises(urllib.error.HTTPError) as caught:
        mp.get_json("/v1/models", auth=False)
    assert caught.value.code in (401, 403)
