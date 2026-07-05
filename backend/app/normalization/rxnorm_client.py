"""Client for the NLM RxNorm REST API — the mandatory normalization step for
any drug/supplement name extracted from a document. Free, no API key.

Uses `approximateTerm.json`, which is specifically built to handle
misspellings, brand names, and loosely-formatted input (exactly what comes
out of OCR/handwriting extraction), rather than requiring an exact string
match.
"""

from __future__ import annotations

import logging

import httpx

from app.schemas import RxNormMatch

logger = logging.getLogger(__name__)


class RxNormClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def find_best_match(self, name: str) -> RxNormMatch:
        """Best-effort normalization. A network/API failure must never sink
        the pipeline - the item is kept unnormalized (zero confidence, so it
        gets flagged for review) and normalization can be retried later."""
        name = name.strip()
        if not name:
            return RxNormMatch(match_score=0.0, normalization_confidence=0.0)

        try:
            return await self._lookup(name)
        except httpx.HTTPError as exc:
            logger.warning("RxNorm unavailable for '%s': %s", name, exc)
            return RxNormMatch(match_score=0.0, normalization_confidence=0.0)

    async def _lookup(self, name: str) -> RxNormMatch:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            candidates = await self._approximate_term(client, name)
            if not candidates:
                return RxNormMatch(match_score=0.0, normalization_confidence=0.0)

            best = max(candidates, key=lambda c: c["score"])
            rxcui = best["rxcui"]
            canonical_name = await self._canonical_name(client, rxcui) or best.get("name")

        score = float(best["score"])
        return RxNormMatch(
            rxcui=rxcui,
            canonical_name=canonical_name,
            match_score=score,
            normalization_confidence=round(min(score, 100.0) / 100.0, 4),
            source="rxnorm",
        )

    async def _approximate_term(self, client: httpx.AsyncClient, name: str) -> list[dict]:
        resp = await client.get(
            f"{self._base_url}/approximateTerm.json",
            params={"term": name, "maxEntries": 5},
        )
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("approximateGroup", {}).get("candidate") or []
        results = []
        for c in candidates:
            rxcui = c.get("rxcui")
            score = c.get("score")
            if rxcui is None or score is None:
                continue
            results.append({"rxcui": rxcui, "score": float(score), "name": c.get("name")})
        return results

    async def _canonical_name(self, client: httpx.AsyncClient, rxcui: str) -> str | None:
        resp = await client.get(f"{self._base_url}/rxcui/{rxcui}/property.json", params={"propName": "RxNorm Name"})
        resp.raise_for_status()
        data = resp.json()
        props = data.get("propConceptGroup", {}).get("propConcept") or []
        if props:
            return props[0].get("propValue")
        return None
