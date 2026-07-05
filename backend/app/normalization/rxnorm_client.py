"""Client for the NLM RxNorm REST API - the mandatory normalization step for
any drug/supplement name extracted from a document. Free, no API key.

Accuracy hardening (from a field audit of real medication charts):
1. `approximateTerm` is queried with option=1 (current concepts only), so
   retired concepts like "INSULIN BEEF LENTE" can never win.
2. Candidates are re-ranked with awareness of the extracted route/form: an
   oral tablet must not map to an "Injection" concept and vice versa.
3. When candidates remain genuinely ambiguous, a cheap text-only LLM call
   adjudicates among the top candidates using the full extracted context.
4. The winning concept is resolved to its ingredient (TTY=IN); brand and
   generic forms share an ingredient, which downstream code uses to
   de-duplicate "Eliquis" vs "apixaban".
"""

from __future__ import annotations

import logging

import httpx

from app.schemas import ExtractedItem, RxNormMatch

logger = logging.getLogger(__name__)

# Tokens that indicate a candidate concept's delivery form. If the extracted
# context clearly says oral and the candidate name says injectable (or the
# reverse), the candidate is heavily penalized.
_INJECTABLE_TOKENS = ("inject", "injection", "injectable", "prefilled", "cartridge",
                      "syringe", "pen inj", "intravenous")
_ORAL_TOKENS = ("oral", "tablet", "capsule", "chewable", "by mouth", "sublingual")
_ROUTE_CONFLICT_PENALTY = 40.0
# If the top two candidates are within this score delta after re-ranking (or
# a route conflict was detected), ask the LLM to adjudicate.
_ADJUDICATION_DELTA = 15.0


def _context_route(item: ExtractedItem | None) -> str | None:
    """'oral' / 'injectable' / None, from the extracted route+form+raw text."""
    if item is None:
        return None
    text = " ".join(filter(None, (item.route, item.form, item.raw_text))).lower()
    if any(t in text for t in _INJECTABLE_TOKENS):
        return "injectable"
    if any(t in text for t in _ORAL_TOKENS):
        return "oral"
    return None


def _candidate_route(name: str) -> str | None:
    lower = name.lower()
    if any(t in lower for t in _INJECTABLE_TOKENS):
        return "injectable"
    if any(t in lower for t in _ORAL_TOKENS):
        return "oral"
    return None


class RxNormClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def find_best_match(
        self, name: str, context: ExtractedItem | None = None
    ) -> RxNormMatch:
        """Best-effort normalization. A network/API failure must never sink
        the pipeline - the item is kept unnormalized (zero confidence, so it
        gets flagged for review) and normalization can be retried later."""
        name = name.strip()
        if not name:
            return RxNormMatch(match_score=0.0, normalization_confidence=0.0)

        try:
            return await self._lookup(name, context)
        except httpx.HTTPError as exc:
            logger.warning("RxNorm unavailable for '%s': %s", name, exc)
            return RxNormMatch(match_score=0.0, normalization_confidence=0.0)

    async def _lookup(self, name: str, context: ExtractedItem | None) -> RxNormMatch:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            candidates = await self._approximate_term(client, name)
            if not candidates:
                return RxNormMatch(match_score=0.0, normalization_confidence=0.0)

            for c in candidates:
                c["name"] = c.get("name") or await self._canonical_name(client, c["rxcui"]) or ""

            ranked, route_conflict = _rerank(candidates, _context_route(context))
            best = ranked[0]

            ambiguous = route_conflict or (
                len(ranked) > 1 and ranked[0]["adjusted"] - ranked[1]["adjusted"] < _ADJUDICATION_DELTA
                and ranked[0]["name"].lower() != ranked[1]["name"].lower()
            )
            if ambiguous and context is not None:
                adjudicated = _adjudicate(context, ranked[:5])
                if adjudicated is not None:
                    best = adjudicated

            canonical_name = best["name"] or await self._canonical_name(client, best["rxcui"])
            ingredient_rxcui, ingredient_name = await self._ingredient(client, best["rxcui"])

        score = float(best["score"])
        return RxNormMatch(
            rxcui=best["rxcui"],
            canonical_name=canonical_name,
            ingredient_rxcui=ingredient_rxcui,
            ingredient_name=ingredient_name,
            match_score=score,
            normalization_confidence=round(min(score, 100.0) / 100.0, 4),
            source="rxnorm",
        )

    async def _approximate_term(self, client: httpx.AsyncClient, name: str) -> list[dict]:
        resp = await client.get(
            f"{self._base_url}/approximateTerm.json",
            # option=1: restrict to current (non-suppressed) concepts, so
            # retired entries like "INSULIN BEEF LENTE" never surface.
            params={"term": name, "maxEntries": 5, "option": 1},
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
        resp = await client.get(
            f"{self._base_url}/rxcui/{rxcui}/property.json", params={"propName": "RxNorm Name"}
        )
        resp.raise_for_status()
        data = resp.json()
        props = data.get("propConceptGroup", {}).get("propConcept") or []
        if props:
            return props[0].get("propValue")
        return None

    async def _ingredient(
        self, client: httpx.AsyncClient, rxcui: str
    ) -> tuple[str | None, str | None]:
        """Resolves the concept to its ingredient (TTY=IN). Brand and generic
        forms of the same drug share an ingredient RxCUI."""
        try:
            resp = await client.get(
                f"{self._base_url}/rxcui/{rxcui}/related.json", params={"tty": "IN"}
            )
            resp.raise_for_status()
            groups = resp.json().get("relatedGroup", {}).get("conceptGroup") or []
            for group in groups:
                for concept in group.get("conceptProperties") or []:
                    return concept.get("rxcui"), concept.get("name")
        except httpx.HTTPError as exc:
            logger.warning("Ingredient lookup failed for rxcui %s: %s", rxcui, exc)
        return None, None


def _rerank(candidates: list[dict], context_route: str | None) -> tuple[list[dict], bool]:
    """Adjusts approximateTerm scores with route/form awareness. Returns the
    re-ranked list and whether any candidate had a route conflict with the
    extracted context (a strong ambiguity signal)."""

    conflict_seen = False
    for c in candidates:
        adjusted = c["score"]
        cand_route = _candidate_route(c["name"] or "")
        if context_route and cand_route and cand_route != context_route:
            adjusted -= _ROUTE_CONFLICT_PENALTY
            conflict_seen = True
        c["adjusted"] = adjusted
    ranked = sorted(candidates, key=lambda c: c["adjusted"], reverse=True)
    return ranked, conflict_seen


def _adjudicate(context: ExtractedItem, candidates: list[dict]) -> dict | None:
    """One cheap text-only LLM call to pick among close candidates using the
    full extracted context. Returns the chosen candidate, or None to keep the
    mechanical ranking (also on any failure - this is best-effort)."""

    import anthropic

    from app.config import get_settings

    settings = get_settings()
    if not settings.anthropic_api_key.strip():
        return None

    numbered = "\n".join(
        f"{i + 1}. {c['name']} (RxCUI {c['rxcui']})" for i, c in enumerate(candidates)
    )
    prompt = (
        "A medication was extracted from a patient document:\n"
        f"- text as written: {context.raw_text!r}\n"
        f"- name: {context.name_as_written!r}, dose: {context.dosage!r}, "
        f"form: {context.form!r}, route: {context.route!r}, "
        f"frequency: {context.frequency!r}\n\n"
        "Which of these RxNorm concepts is the correct match?\n"
        f"{numbered}\n\n"
        "Answer with ONLY the number of the best match, or 0 if none of them "
        "is the same drug and form."
    )
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=settings.extraction_model,
            max_tokens=8,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        choice = int(text.split()[0])
        if 1 <= choice <= len(candidates):
            logger.info(
                "Adjudicated '%s' -> %s", context.name_as_written, candidates[choice - 1]["name"]
            )
            return candidates[choice - 1]
        return None
    except Exception:
        logger.warning("Adjudication failed for '%s'", context.name_as_written, exc_info=True)
        return None
