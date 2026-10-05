"""One real run against the real API, so the unknowns stop being unknown.

Everything else in this project is tested with the model calls stubbed, which
proves the orchestration and proves nothing about the prompts. This exercises
the live path on a sample regimen and prints what actually came back, plus
latency and token counts per stage, so output quality and cost can be judged
rather than guessed.

    python -m app.smoke             # the assistant only - one call, seconds
    python -m app.smoke --panel     # adds the full eight-round panel

It uses a throwaway database and never touches real records.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path

from app.config import get_settings
from app.panel import llm
from app.schemas import ItemStatus, LabFlag, LabResult, MedListItem, RecordKind

RULE = "=" * 72


def _sample_regimen() -> tuple[list[MedListItem], list[LabResult]]:
    """A deliberately awkward little regimen: a genuine interaction worth
    discussing, a supplement people forget to mention, an unexplained drug,
    and one lab that is actually out of range."""
    items = [
        MedListItem(kind=RecordKind.medicine, name="Warfarin", canonical_name="warfarin",
                    dosage="5 mg", frequency="daily"),
        MedListItem(kind=RecordKind.supplement, name="Fish Oil",
                    canonical_name="Omega-3 Fatty Acids", dosage="1000 mg", frequency="daily"),
        MedListItem(kind=RecordKind.medicine, name="Lisinopril", canonical_name="lisinopril",
                    dosage="10 mg", frequency="daily"),
        MedListItem(kind=RecordKind.medicine, name="Ibuprofen", canonical_name="ibuprofen",
                    dosage="400 mg", frequency="as needed"),
        MedListItem(kind=RecordKind.medicine, name="Old Antibiotic",
                    status=ItemStatus.stopped),
    ]
    labs = [
        LabResult(name="INR", value="2.4", reference_range="2.0-3.0", flag=LabFlag.normal,
                  collected_date="2026-09-20", extraction_confidence=0.96),
        LabResult(name="eGFR", value="48", unit="mL/min", reference_range=">60",
                  flag=LabFlag.low, collected_date="2026-09-20", extraction_confidence=0.94),
    ]
    return items, labs


def _usage_line(usage: llm.Usage, seconds: float) -> str:
    lines = [
        f"  {usage.calls} API call(s) in {seconds:.1f}s - "
        f"{usage.input_tokens:,} input + {usage.output_tokens:,} output tokens"
    ]
    for model, m in sorted(usage.by_model.items()):
        lines.append(f"    {model}: {m['calls']} call(s), {m['in']:,} in / {m['out']:,} out")
    lines.append("  (check current per-token pricing at https://anthropic.com/pricing)")
    return "\n".join(lines)


async def _run_assistant(items, labs) -> None:
    from app.assistant import engine as assistant

    questions = [
        "Why am I on lisinopril?",
        "Is it okay to take ibuprofen with what I'm already on?",
    ]

    for question in questions:
        print(f"\n{RULE}\nYOU: {question}\n{RULE}")
        usage = llm.start_usage()
        started = time.monotonic()
        turn = await assistant.answer(
            question, items=items, labs=labs, history=[], observations=[], findings=[]
        )
        elapsed = time.monotonic() - started

        print(f"\n{turn.text}\n")
        if turn.citations:
            print("  Cited: " + " | ".join(c.label for c in turn.citations))
        for q in turn.questions_for_clinician:
            print(f"  Ask a clinician: \"{q}\"")
        for action in turn.actions:
            print(f"  Proposed change: {action.description}")
        if turn.guard_notice:
            print(f"  !! GUARD FIRED: {turn.guard_notice}")
        print()
        print(_usage_line(usage, elapsed))


async def _run_panel(items, labs) -> None:
    from app.interactions.engine import evaluate
    from app.panel import engine as panel
    from app.storage.med_list import item_to_record

    print(f"\n{RULE}\nEIGHT-ROUND PANEL\n{RULE}")
    usage = llm.start_usage()
    started = time.monotonic()

    rounds: list[str] = []
    review = await panel.run_panel(
        items, [], evaluate([item_to_record(i) for i in items if i.status == ItemStatus.active]),
        note="I've been bruising more easily the last few weeks.",
        emit=lambda e: rounds.append(f"  round {e['n']}: {e['name']}")
        if e.get("type") == "round" else None,
        labs=labs,
    )
    elapsed = time.monotonic() - started

    print("\n".join(rounds))
    if review.halted:
        print(f"\n  HALTED ({review.halt_reason}): {review.urgency_message}")
        return

    print(f"\n  Seated: {', '.join(s.name for s in review.seated)}")
    print(f"  Not seated: {', '.join(s.name for s in review.not_seated)}")
    print(f"\n{review.headline}\n\n{review.summary}\n")

    for concern in review.concerns:
        print(f"  AGREED: {concern.title} [{concern.evidence_grade.value}, "
              f"{concern.confidence:.0%}] - {concern.detail}")
    for dropped in review.dropped:
        print(f"  DROPPED: {dropped.title} - {dropped.reason}")
    for d in review.dissent:
        print(f"  UNRESOLVED: {d.topic}\n    A: {d.side_a}\n    B: {d.side_b}"
              f"\n    settles it: {d.what_would_settle_it}")
    for q in review.questions:
        print(f"  ASK ({q.tier.value}): \"{q.question}\"")
    print(f"\n  Model-correlation note: {review.correlated_model_note}")
    print()
    print(_usage_line(usage, elapsed))


async def _main(run_panel: bool) -> int:
    settings = get_settings()
    if not settings.anthropic_api_key.strip():
        print("No ANTHROPIC_API_KEY is configured - there is nothing to smoke-test.")
        print("Add one to backend/.env and run this again.")
        return 1

    print(f"{RULE}\nSMOKE TEST - real API calls against a sample regimen")
    print(f"Models: extraction={settings.extraction_model} panel={settings.panel_model}")
    print(f"        synthesis={settings.panel_synthesis_model} assistant={settings.assistant_model}")
    print(f"{RULE}")

    items, labs = _sample_regimen()
    print("\nSample regimen:")
    for item in items:
        state = " (stopped)" if item.status == ItemStatus.stopped else ""
        print(f"  - {item.name} {item.dosage or ''} {item.frequency or ''}{state}".rstrip())
    for lab in labs:
        print(f"  - lab: {lab.name} {lab.display} [{lab.flag.value}]")

    await _run_assistant(items, labs)
    if run_panel:
        await _run_panel(items, labs)
    else:
        print(f"\n{RULE}\nSkipped the panel (it costs appreciably more). "
              f"Add --panel to include it.\n{RULE}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", action="store_true",
                        help="also run the full eight-round panel (more calls, more cost)")
    args = parser.parse_args()

    # A throwaway database, so a smoke test can never touch real records.
    import os

    os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "smoke.sqlite3")
    get_settings.cache_clear()

    return asyncio.run(_main(args.panel))


if __name__ == "__main__":
    sys.exit(main())
