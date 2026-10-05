"""Startup checks that fail in plain English, before anything confusing happens.

The failure this exists to prevent: a stale or mistyped model id in `.env`
looks completely fine until the first document upload, and then surfaces as a
404 from an API the user has never heard of. Checking it at launch costs one
zero-token request and turns that into a sentence naming the setting, the bad
value, and what to put instead.

Run directly (`python -m app.preflight`) or over HTTP (`GET /api/preflight`).
"""

from __future__ import annotations

import difflib
import sys
from dataclasses import dataclass, field

import anthropic
import httpx

from app.config import get_settings

_TIMEOUT = 12.0  # a hung network call must never stall startup


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    fatal: bool = False  # true when this will cause a confusing failure later


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    available_models: list[str] = field(default_factory=list)
    degraded: bool = False  # usable, but document reading is off

    @property
    def ok(self) -> bool:
        return not any(c.fatal for c in self.checks)

    def add(self, *checks: Check) -> None:
        self.checks.extend(checks)


def _configured_models(settings) -> dict[str, str]:
    """Every model id the app will actually try to use, by its setting name."""
    return {
        "EXTRACTION_MODEL": settings.extraction_model,
        "PANEL_MODEL": settings.panel_model,
        "PANEL_SYNTHESIS_MODEL": settings.panel_synthesis_model,
        "ASSISTANT_MODEL": settings.assistant_model,
    }


def _suggest(wanted: str, available: list[str]) -> str:
    """The nearest real model id, so the message says what to put instead
    rather than only what is wrong."""
    if not available:
        return ""
    close = difflib.get_close_matches(wanted, available, n=1, cutoff=0.5)
    if close:
        return close[0]
    # Fall back to the newest id sharing the family prefix ("claude-sonnet").
    family = "-".join(wanted.split("-")[:2])
    same = sorted(m for m in available if m.startswith(family))
    return same[-1] if same else ""


def _check_models(settings, report: Report) -> None:
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=_TIMEOUT)

    try:
        listing = client.models.list(limit=100)
        report.available_models = [m.id for m in listing.data]
    except anthropic.AuthenticationError:
        report.add(Check(
            "API key", False,
            "The Anthropic API key was rejected.",
            "Check it at https://console.anthropic.com (Settings > API keys), then "
            "put the correct value in backend\\.env as ANTHROPIC_API_KEY=sk-ant-...",
            fatal=True,
        ))
        return
    except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
        report.add(Check(
            "Anthropic API", False,
            f"Could not reach the Anthropic API ({type(exc).__name__}).",
            "Check your internet connection. If you are behind a corporate proxy or "
            "VPN, that may be blocking it.",
            fatal=True,
        ))
        return
    except anthropic.APIError as exc:
        report.add(Check(
            "Anthropic API", False,
            f"The Anthropic API returned an error: {getattr(exc, 'message', str(exc))[:200]}",
            "If this persists, check https://status.anthropic.com",
            fatal=True,
        ))
        return

    report.add(Check(
        "API key", True,
        f"Valid. {len(report.available_models)} model(s) available to this account.",
    ))

    for setting, model_id in _configured_models(settings).items():
        if model_id in report.available_models:
            report.add(Check(f"Model: {setting}", True, f"{model_id} is available."))
            continue

        suggestion = _suggest(model_id, report.available_models)
        fix = f"Edit backend\\.env and set {setting}={suggestion}" if suggestion else (
            f"Edit backend\\.env and set {setting} to one of the models listed below."
        )
        report.add(Check(
            f"Model: {setting}", False,
            f'"{model_id}" is not available to this account.',
            fix, fatal=True,
        ))


def _check_rxnorm(settings, report: Report) -> None:
    """Drug-name standardisation. A failure here degrades accuracy rather than
    stopping the app, so it is reported but never fatal."""
    try:
        response = httpx.get(
            f"{settings.rxnorm_base_url}/version.json", timeout=_TIMEOUT
        )
        response.raise_for_status()
        report.add(Check("RxNorm", True, "Reachable (drug-name standardisation)."))
    except Exception as exc:  # noqa: BLE001 - any failure here is the same story
        report.add(Check(
            "RxNorm", False,
            f"Not reachable ({type(exc).__name__}). The app still works; brand and "
            "generic names may not be matched to each other.",
            "Usually temporary. Check https://rxnav.nlm.nih.gov if it persists.",
        ))


def run(check_network: bool = True) -> Report:
    settings = get_settings()
    report = Report()

    if not settings.anthropic_api_key.strip():
        report.degraded = True
        report.add(Check(
            "API key", False,
            "No Anthropic API key is configured, so reading documents and the "
            "assistant are both turned off.",
            "Add one to backend\\.env as ANTHROPIC_API_KEY=sk-ant-... "
            "Everything else - adding medicines by hand, interaction warnings, "
            "and the PDFs - works without it.",
        ))
        return report

    if check_network:
        _check_models(settings, report)
        _check_rxnorm(settings, report)
    return report


# ----------------------------------------------------------------- CLI output
#
# ASCII only and no colour: this runs inside the launcher's cmd.exe window,
# where anything fancier arrives as mojibake.

def format_report(report: Report) -> str:
    lines = []
    for check in report.checks:
        marker = "[ ok ]" if check.ok else ("[FAIL]" if check.fatal else "[warn]")
        lines.append(f" {marker}  {check.name}: {check.detail}")
        if check.fix and not check.ok:
            lines.append(f"         -> {check.fix}")

    if not report.ok and report.available_models:
        lines.append("")
        lines.append(" Models available to this account:")
        lines += [f"   - {m}" for m in sorted(report.available_models)]

    if report.degraded:
        lines.append("")
        lines.append(" Starting without document reading. That is fine if it is what you meant.")
    elif not report.ok:
        lines.append("")
        lines.append(" The app will still start, but the parts above will not work")
        lines.append(" until that is fixed.")

    return "\n".join(lines)


def main() -> int:
    report = run()
    print(format_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
