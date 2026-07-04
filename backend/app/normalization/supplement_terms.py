"""Small curated fallback table for common dietary supplements that RxNorm
either doesn't recognize or only weakly matches (many herbal/nutraceutical
names aren't in RxNorm at all).

This is an explicit MVP stub. Production should replace/augment this with
TRC Healthcare's NatMed Pro API, which is the actual evidence-based authority
on supplements — see docs/ARCHITECTURE.md section 2.5.
"""

from __future__ import annotations

from app.schemas import RxNormMatch

# lowercase alias -> canonical display name
_SUPPLEMENT_SYNONYMS: dict[str, str] = {
    "vitamin d": "Vitamin D",
    "vitamin d3": "Vitamin D3 (Cholecalciferol)",
    "vitamin d2": "Vitamin D2 (Ergocalciferol)",
    "vitamin c": "Vitamin C (Ascorbic Acid)",
    "vitamin b12": "Vitamin B12 (Cobalamin)",
    "b12": "Vitamin B12 (Cobalamin)",
    "vitamin b6": "Vitamin B6 (Pyridoxine)",
    "folate": "Folate",
    "folic acid": "Folic Acid",
    "magnesium glycinate": "Magnesium Glycinate",
    "magnesium citrate": "Magnesium Citrate",
    "magnesium oxide": "Magnesium Oxide",
    "fish oil": "Fish Oil (Omega-3)",
    "omega 3": "Omega-3 Fatty Acids",
    "omega-3": "Omega-3 Fatty Acids",
    "coq10": "Coenzyme Q10",
    "coenzyme q10": "Coenzyme Q10",
    "zinc": "Zinc",
    "iron": "Iron",
    "calcium": "Calcium",
    "probiotic": "Probiotic",
    "probiotics": "Probiotic",
    "melatonin": "Melatonin",
    "turmeric": "Turmeric (Curcumin)",
    "curcumin": "Curcumin",
    "ashwagandha": "Ashwagandha",
    "st johns wort": "St. John's Wort",
    "st. john's wort": "St. John's Wort",
    "milk thistle": "Milk Thistle",
    "glucosamine": "Glucosamine",
    "collagen": "Collagen",
    "biotin": "Biotin",
    "iodine": "Iodine",
    "potassium": "Potassium",
    "vitamin a": "Vitamin A",
    "vitamin e": "Vitamin E",
    "vitamin k": "Vitamin K",
    "vitamin k2": "Vitamin K2 (Menaquinone)",
    "niacin": "Niacin (Vitamin B3)",
    "l-theanine": "L-Theanine",
    "5-htp": "5-HTP",
    "creatine": "Creatine",
    "nac": "N-Acetylcysteine (NAC)",
}


def lookup(name: str) -> RxNormMatch | None:
    key = name.strip().lower()
    canonical = _SUPPLEMENT_SYNONYMS.get(key)
    if canonical is None:
        return None
    return RxNormMatch(
        rxcui=None,
        canonical_name=canonical,
        match_score=100.0,
        normalization_confidence=0.75,
        source="local_supplement_table",
    )
