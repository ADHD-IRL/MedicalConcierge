import httpx
import pytest

import app.normalization.rxnorm_client as rc
from app.normalization.rxnorm_client import RxNormClient, _rerank
from app.schemas import ExtractedItem, SourceType

BASE = "https://rxnav.nlm.nih.gov/REST"


def _ctx(route=None, form=None, raw="med 40mg"):
    return ExtractedItem(
        raw_text=raw, name_as_written="med", route=route, form=form,
        source_type=SourceType.printed_document, extraction_confidence=0.9, ambiguities=[],
    )


def _fake_transport(monkeypatch, approx_candidates, ingredient=None, capture=None):
    async def fake_get(self, url, params=None):
        if capture is not None:
            capture.append((url, params))
        if "approximateTerm" in url:
            body = {"approximateGroup": {"candidate": approx_candidates}}
        elif "related.json" in url:
            props = [{"rxcui": ingredient[0], "name": ingredient[1]}] if ingredient else []
            body = {"relatedGroup": {"conceptGroup": [{"conceptProperties": props}]}}
        elif "property.json" in url:
            body = {"propConceptGroup": {"propConcept": [{"propValue": "resolved-name"}]}}
        else:
            raise AssertionError(f"unexpected url {url}")
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)


@pytest.mark.asyncio
async def test_picks_highest_score_and_resolves_ingredient(monkeypatch):
    capture = []
    _fake_transport(
        monkeypatch,
        [{"rxcui": "202433", "score": "100", "name": "acetaminophen"},
         {"rxcui": "161", "score": "80", "name": "acetaminophen 500 MG"}],
        ingredient=("161", "acetaminophen"),
        capture=capture,
    )

    match = await RxNormClient(BASE).find_best_match("tylenol")

    assert match.rxcui == "202433"
    assert match.ingredient_rxcui == "161"
    assert match.ingredient_name == "acetaminophen"
    approx_calls = [p for u, p in capture if "approximateTerm" in u]
    assert approx_calls[0]["option"] == 1  # current-concepts-only filter


@pytest.mark.asyncio
async def test_oral_context_demotes_injection_candidate(monkeypatch):
    _fake_transport(
        monkeypatch,
        [{"rxcui": "inj1", "score": "100", "name": "pantoprazole Injection [Protonix]"},
         {"rxcui": "oral1", "score": "95", "name": "pantoprazole 40 MG Delayed Release Oral Tablet"}],
        ingredient=("7646", "pantoprazole"),
    )
    monkeypatch.setattr(rc, "_adjudicate", lambda ctx, cands: None)  # keep mechanical pick

    match = await RxNormClient(BASE).find_best_match(
        "pantoprazole", context=_ctx(route="by mouth", form="tablet")
    )

    assert match.rxcui == "oral1"


@pytest.mark.asyncio
async def test_adjudication_invoked_only_when_ambiguous(monkeypatch):
    calls = []

    def fake_adjudicate(ctx, cands):
        calls.append([c["rxcui"] for c in cands])
        return cands[1]  # LLM picks the second candidate

    monkeypatch.setattr(rc, "_adjudicate", fake_adjudicate)

    # close scores -> ambiguous -> adjudicated
    _fake_transport(
        monkeypatch,
        [{"rxcui": "a", "score": "90", "name": "insulin glargine 100 UNT/ML Injection"},
         {"rxcui": "b", "score": "85", "name": "insulin aspart 100 UNT/ML Injection"}],
        ingredient=("x", "insulin aspart"),
    )
    match = await RxNormClient(BASE).find_best_match("novalog 100u/ml", context=_ctx())
    assert match.rxcui == "b"
    assert len(calls) == 1

    # clear winner, no conflict -> no adjudication
    calls.clear()
    _fake_transport(
        monkeypatch,
        [{"rxcui": "m1", "score": "100", "name": "metformin 500 MG Oral Tablet"},
         {"rxcui": "m2", "score": "60", "name": "metformin hydrochloride 850 MG Oral Tablet"}],
        ingredient=("6809", "metformin"),
    )
    match = await RxNormClient(BASE).find_best_match("metformin", context=_ctx())
    assert match.rxcui == "m1"
    assert calls == []


def test_rerank_is_symmetric():
    candidates = [
        {"rxcui": "1", "score": 100.0, "name": "drug Oral Tablet"},
        {"rxcui": "2", "score": 95.0, "name": "drug Injection"},
    ]
    ranked, conflict = _rerank([dict(c) for c in candidates], "injectable")
    assert ranked[0]["rxcui"] == "2"
    assert conflict is True

    ranked2, conflict2 = _rerank([dict(c) for c in candidates], None)
    assert ranked2[0]["rxcui"] == "1"
    assert conflict2 is False


@pytest.mark.asyncio
async def test_no_candidates_and_empty_name(monkeypatch):
    _fake_transport(monkeypatch, [])
    match = await RxNormClient(BASE).find_best_match("some illegible scrawl")
    assert match.rxcui is None and match.normalization_confidence == 0.0

    match2 = await RxNormClient(BASE).find_best_match("   ")
    assert match2.rxcui is None


@pytest.mark.asyncio
async def test_network_failure_degrades_gracefully(monkeypatch):
    async def exploding_get(self, url, params=None):
        raise httpx.ConnectError("no network", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", exploding_get)

    match = await RxNormClient(BASE).find_best_match("metformin")

    assert match.rxcui is None
    assert match.normalization_confidence == 0.0
