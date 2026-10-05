"""Startup checks. The failure these exist to prevent is a stale model id in
.env, which looks fine until the first upload and then surfaces as a 404 from
an API the user has never heard of."""

import types

import anthropic
import pytest
from fastapi.testclient import TestClient

from app import preflight
from app.config import Settings, get_settings
from app.main import app


def _settings(**over):
    base = dict(
        anthropic_api_key="sk-ant-test",
        extraction_model="claude-sonnet-5",
        panel_model="claude-sonnet-5",
        panel_synthesis_model="claude-opus-5",
        assistant_model="claude-sonnet-5",
    )
    base.update(over)
    return Settings(**base)


def _fake_client(model_ids=None, raises=None):
    class Models:
        def list(self, limit=100):
            if raises:
                raise raises
            return types.SimpleNamespace(
                data=[types.SimpleNamespace(id=m) for m in (model_ids or [])]
            )

    return types.SimpleNamespace(models=Models())


@pytest.fixture
def no_rxnorm(monkeypatch):
    """RxNorm is checked separately; silence it so model assertions stand alone."""
    monkeypatch.setattr(preflight, "_check_rxnorm", lambda settings, report: None)


def test_no_key_is_degraded_not_broken(monkeypatch):
    monkeypatch.setattr(preflight, "get_settings", lambda: _settings(anthropic_api_key=""))
    report = preflight.run()

    assert report.degraded
    assert report.ok  # nothing fatal - manual entry, warnings and PDFs all still work
    assert "works without it" in report.checks[0].fix


def test_all_models_available(monkeypatch, no_rxnorm):
    monkeypatch.setattr(preflight, "get_settings", _settings)
    monkeypatch.setattr(anthropic, "Anthropic",
                        lambda **kw: _fake_client(["claude-sonnet-5", "claude-opus-5"]))

    report = preflight.run()
    assert report.ok
    assert all(c.ok for c in report.checks)


def test_a_stale_model_id_is_caught_and_a_replacement_suggested(monkeypatch, no_rxnorm):
    """The headline case: the configured id no longer exists."""
    monkeypatch.setattr(preflight, "get_settings", _settings)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _fake_client(
        ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5-20251001"]
    ))

    report = preflight.run()
    assert not report.ok

    failed = [c for c in report.checks if not c.ok]
    assert {c.name for c in failed} == {
        "Model: EXTRACTION_MODEL", "Model: PANEL_MODEL",
        "Model: PANEL_SYNTHESIS_MODEL", "Model: ASSISTANT_MODEL",
    }
    # It names what to put instead, not merely what is wrong.
    extraction = next(c for c in failed if c.name == "Model: EXTRACTION_MODEL")
    assert "claude-sonnet-5-5" in extraction.fix
    assert "EXTRACTION_MODEL=" in extraction.fix

    synthesis = next(c for c in failed if c.name == "Model: PANEL_SYNTHESIS_MODEL")
    assert "claude-opus-5-5" in synthesis.fix


def test_suggestion_falls_back_to_the_model_family():
    """No close string match, but the same family exists."""
    assert preflight._suggest(
        "claude-sonnet-9", ["claude-haiku-4-5-20251001", "claude-sonnet-5-5"]
    ) == "claude-sonnet-5-5"
    assert preflight._suggest("claude-sonnet-5", []) == ""


def test_rejected_key_is_fatal_and_says_where_to_look(monkeypatch, no_rxnorm):
    monkeypatch.setattr(preflight, "get_settings", _settings)
    import httpx

    request = httpx.Request("GET", "https://api.anthropic.com/v1/models")
    err = anthropic.AuthenticationError(
        "bad key", response=httpx.Response(401, request=request), body=None
    )
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _fake_client(raises=err))

    report = preflight.run()
    assert not report.ok
    assert "console.anthropic.com" in report.checks[0].fix


def test_network_failure_is_fatal_but_names_the_likely_cause(monkeypatch, no_rxnorm):
    monkeypatch.setattr(preflight, "get_settings", _settings)
    import httpx

    err = anthropic.APIConnectionError(
        request=httpx.Request("GET", "https://api.anthropic.com/v1/models")
    )
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _fake_client(raises=err))

    report = preflight.run()
    assert not report.ok
    assert "proxy" in report.checks[0].fix.lower()


def test_rxnorm_failure_never_blocks_startup(monkeypatch):
    monkeypatch.setattr(preflight, "get_settings", _settings)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _fake_client(["claude-sonnet-5", "claude-opus-5"]))

    def boom(url, timeout=None):
        raise OSError("no route to host")

    monkeypatch.setattr(preflight.httpx, "get", boom)
    report = preflight.run()

    rxnorm = next(c for c in report.checks if c.name == "RxNorm")
    assert not rxnorm.ok and not rxnorm.fatal
    assert report.ok  # degrades accuracy, does not stop the app


def test_report_is_ascii_only_for_the_windows_console(monkeypatch, no_rxnorm):
    """The launcher runs this in cmd.exe, where anything fancier is mojibake."""
    monkeypatch.setattr(preflight, "get_settings", _settings)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _fake_client(["claude-sonnet-5-5"]))

    text = preflight.format_report(preflight.run())
    text.encode("ascii")  # raises if anything non-ASCII crept in
    assert "[FAIL]" in text and "Models available to this account:" in text


def test_cli_exit_codes(monkeypatch, no_rxnorm, capsys):
    monkeypatch.setattr(preflight, "get_settings", _settings)

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _fake_client(["claude-sonnet-5", "claude-opus-5"]))
    assert preflight.main() == 0

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _fake_client(["something-else"]))
    assert preflight.main() == 1
    assert "[FAIL]" in capsys.readouterr().out


def test_preflight_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "p.sqlite3"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()

    body = TestClient(app).get("/api/preflight").json()
    assert body["ok"] is True and body["degraded"] is True
    assert body["checks"][0]["name"] == "API key"
    get_settings.cache_clear()


# ------------------------------------------------------------------- smoke

def test_smoke_runs_the_real_code_path_with_a_stubbed_model(monkeypatch, capsys):
    """The smoke test's own plumbing has to work, or the one real run the
    user makes will fail for a reason that has nothing to do with the app."""
    import asyncio

    from app import smoke
    from app.panel import llm
    from tests.test_panel import _fake_structured_for

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    get_settings.cache_clear()

    calls = {"n": 0}

    async def fake_structured(system, user, schema, *, gate, max_tokens=4000, model=None):
        calls["n"] += 1
        req = set(schema.get("required", []))
        if "answer" in req:
            return {
                "answer": "Your records don't say why lisinopril was started.",
                "citations": [], "questions_for_clinician": ["Why am I on lisinopril?"],
                "actions": [],
            }
        return _fake_structured_for(schema)

    async def fake_prose(system, user, *, gate, max_tokens=1200, model=None):
        calls["n"] += 1
        return "A take."

    monkeypatch.setattr(llm, "structured", fake_structured)
    monkeypatch.setattr(llm, "prose", fake_prose)

    assert asyncio.run(smoke._main(run_panel=True)) == 0
    out = capsys.readouterr().out

    # The assistant leg
    assert "Why am I on lisinopril?" in out
    assert "Ask a clinician:" in out
    # The panel leg, all eight rounds
    for n in range(1, 9):
        assert f"round {n}:" in out
    assert "UNRESOLVED:" in out and "Model-correlation note:" in out
    assert "API call(s) in" in out
    get_settings.cache_clear()


def test_smoke_without_a_key_exits_nonzero(monkeypatch, capsys):
    import asyncio

    from app import smoke

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    assert asyncio.run(smoke._main(run_panel=False)) == 1
    assert "nothing to smoke-test" in capsys.readouterr().out
    get_settings.cache_clear()


def test_usage_tally_is_per_task_and_survives_concurrency():
    """Rounds run in parallel; the tally must not be a shared global that one
    task resets under another."""
    import asyncio
    import types

    from app.panel import llm

    async def worker(n):
        usage = llm.start_usage()
        for _ in range(n):
            await asyncio.sleep(0)
            llm._record("m", types.SimpleNamespace(
                usage=types.SimpleNamespace(input_tokens=10, output_tokens=5)))
        return usage.calls

    assert asyncio.run(_gather(worker)) == [2, 5]


async def _gather(worker):
    return list(await __import__("asyncio").gather(worker(2), worker(5)))
