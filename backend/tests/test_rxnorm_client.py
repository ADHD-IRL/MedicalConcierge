import httpx
import pytest

from app.normalization.rxnorm_client import RxNormClient

APPROX_RESPONSE = {
    "approximateGroup": {
        "candidate": [
            {"rxcui": "202433", "score": "100", "name": "acetaminophen"},
            {"rxcui": "161", "score": "80", "name": "acetaminophen 500 MG"},
        ]
    }
}

PROPERTY_RESPONSE = {
    "propConceptGroup": {"propConcept": [{"propValue": "acetaminophen"}]}
}


@pytest.mark.asyncio
async def test_find_best_match_picks_highest_score(monkeypatch):
    async def fake_get(self, url, params=None):
        if "approximateTerm" in url:
            return httpx.Response(200, json=APPROX_RESPONSE, request=httpx.Request("GET", url))
        if "property" in url:
            return httpx.Response(200, json=PROPERTY_RESPONSE, request=httpx.Request("GET", url))
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    client = RxNormClient(base_url="https://rxnav.nlm.nih.gov/REST")
    match = await client.find_best_match("tylenol")

    assert match.rxcui == "202433"
    assert match.canonical_name == "acetaminophen"
    assert match.match_score == 100.0
    assert match.normalization_confidence == 1.0


@pytest.mark.asyncio
async def test_find_best_match_no_candidates(monkeypatch):
    async def fake_get(self, url, params=None):
        return httpx.Response(
            200, json={"approximateGroup": {}}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    client = RxNormClient(base_url="https://rxnav.nlm.nih.gov/REST")
    match = await client.find_best_match("some illegible scrawl")

    assert match.rxcui is None
    assert match.normalization_confidence == 0.0


@pytest.mark.asyncio
async def test_find_best_match_empty_name_short_circuits():
    client = RxNormClient(base_url="https://rxnav.nlm.nih.gov/REST")
    match = await client.find_best_match("   ")

    assert match.rxcui is None
    assert match.normalization_confidence == 0.0
