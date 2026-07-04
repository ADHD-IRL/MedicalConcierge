"""Curated interaction knowledge base for the MVP screening engine.

Every rule here is a widely documented, pharmacy-handout-level fact - the
kind of warning printed on the bag your prescription comes in. This is an
explicit starter set, not a complete interaction database: the production
upgrade path (docs/ARCHITECTURE.md section 2.3) swaps/augments this with
openFDA prescribing-label data and TRC NatMed Pro for supplements, behind
the same rule interface.

Matching is term-based against the normalized (RxNorm canonical) name with
the as-written name as fallback, using word boundaries so "iron" never
matches inside another word.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas import FindingCategory, FindingSeverity


@dataclass(frozen=True)
class InteractionRule:
    rule_id: str
    severity: FindingSeverity
    category: FindingCategory
    a_terms: tuple[str, ...]
    b_terms: tuple[str, ...]
    title: str
    explanation: str
    recommendation: str


@dataclass(frozen=True)
class DepletionRule:
    rule_id: str
    drug_terms: tuple[str, ...]
    drug_label: str
    nutrient: str
    nutrient_terms: tuple[str, ...]  # to detect the user already supplements it
    explanation: str
    recommendation_if_missing: str
    evidence_note: str = "Well-documented nutrient depletion (built-in reference list)."


# --- term groups reused across rules -----------------------------------------

ACE_ARB = ("lisinopril", "enalapril", "ramipril", "benazepril", "quinapril",
           "losartan", "valsartan", "olmesartan", "irbesartan", "candesartan")
POTASSIUM_SPARING = ("spironolactone", "eplerenone", "amiloride", "triamterene")
SSRI = ("sertraline", "fluoxetine", "escitalopram", "citalopram", "paroxetine",
        "fluvoxamine")
NSAID = ("ibuprofen", "naproxen", "diclofenac", "meloxicam", "indomethacin",
         "ketorolac", "celecoxib", "aspirin")
ANTICOAGULANT = ("warfarin", "apixaban", "rivaroxaban", "dabigatran", "edoxaban",
                 "clopidogrel", "enoxaparin")
BENZO_SEDATIVE = ("alprazolam", "lorazepam", "diazepam", "clonazepam", "temazepam",
                  "zolpidem", "eszopiclone")
OPIOID = ("oxycodone", "hydrocodone", "morphine", "tramadol", "codeine",
          "hydromorphone", "fentanyl")
PPI = ("omeprazole", "esomeprazole", "pantoprazole", "lansoprazole", "rabeprazole")
STATIN = ("atorvastatin", "simvastatin", "rosuvastatin", "pravastatin", "lovastatin",
          "pitavastatin")
TETRA_QUINOLONE = ("ciprofloxacin", "levofloxacin", "moxifloxacin", "doxycycline",
                   "tetracycline", "minocycline")
DIVALENT_MINERALS = ("calcium", "iron", "magnesium", "zinc")
ST_JOHNS_WORT = ("st. john", "st john")
SEROTONERGIC_SUPP = ("5-htp", "5 htp", "l-tryptophan")
BLEEDING_RISK_SUPP = ("fish oil", "omega-3", "omega 3", "turmeric", "curcumin",
                      "ginkgo", "garlic supplement", "vitamin e")
DIURETIC_LOOP = ("furosemide", "bumetanide", "torsemide")
DIURETIC_THIAZIDE = ("hydrochlorothiazide", "chlorthalidone", "indapamide")
CORTICOSTEROID = ("prednisone", "prednisolone", "methylprednisolone", "dexamethasone")

_DISCUSS = "Do not stop or change anything on your own - bring this up with your doctor or pharmacist."


INTERACTION_RULES: tuple[InteractionRule, ...] = (
    # --- drug + drug ----------------------------------------------------------
    InteractionRule(
        rule_id="anticoag-nsaid",
        severity=FindingSeverity.major,
        category=FindingCategory.drug_drug,
        a_terms=ANTICOAGULANT,
        b_terms=NSAID,
        title="Blood thinner + NSAID pain reliever - bleeding risk",
        explanation="Taking an NSAID (like ibuprofen, naproxen, or aspirin) together "
        "with a blood thinner significantly raises the risk of serious bleeding, "
        "especially stomach bleeding.",
        recommendation="Ask your doctor or pharmacist which pain reliever is safe for "
        f"you (acetaminophen is often suggested instead). {_DISCUSS}",
    ),
    InteractionRule(
        rule_id="ace-potassium-sparing",
        severity=FindingSeverity.moderate,
        category=FindingCategory.drug_drug,
        a_terms=ACE_ARB,
        b_terms=POTASSIUM_SPARING,
        title="Blood pressure medicine + potassium-sparing diuretic - high potassium risk",
        explanation="Both of these medicines raise potassium levels. Together they can "
        "push potassium too high (hyperkalemia), which can affect heart rhythm.",
        recommendation="This combination is sometimes intentional, but it needs periodic "
        f"blood tests for potassium and kidney function. Confirm both prescribers know. {_DISCUSS}",
    ),
    InteractionRule(
        rule_id="ace-nsaid",
        severity=FindingSeverity.moderate,
        category=FindingCategory.drug_drug,
        a_terms=ACE_ARB,
        b_terms=NSAID,
        title="Blood pressure medicine + NSAID - kidney strain",
        explanation="NSAIDs can reduce how well ACE inhibitors/ARBs work and, used "
        "together regularly, put extra strain on the kidneys.",
        recommendation=f"Occasional use may be fine; regular use deserves a check-in. {_DISCUSS}",
    ),
    InteractionRule(
        rule_id="ssri-nsaid",
        severity=FindingSeverity.moderate,
        category=FindingCategory.drug_drug,
        a_terms=SSRI,
        b_terms=NSAID,
        title="SSRI antidepressant + NSAID - stomach bleeding risk",
        explanation="SSRIs and NSAIDs each slightly raise bleeding risk; together the "
        "risk of stomach/intestinal bleeding is meaningfully higher.",
        recommendation=f"Ask whether a stomach protector or different pain reliever makes sense. {_DISCUSS}",
    ),
    InteractionRule(
        rule_id="ssri-tramadol",
        severity=FindingSeverity.major,
        category=FindingCategory.drug_drug,
        a_terms=SSRI,
        b_terms=("tramadol",),
        title="SSRI antidepressant + tramadol - serotonin syndrome risk",
        explanation="Both raise serotonin. Together they can cause serotonin syndrome "
        "(agitation, rapid heart rate, high temperature) - uncommon but serious.",
        recommendation=f"Make sure the prescriber of each knows about the other. {_DISCUSS}",
    ),
    InteractionRule(
        rule_id="benzo-opioid",
        severity=FindingSeverity.major,
        category=FindingCategory.drug_drug,
        a_terms=BENZO_SEDATIVE,
        b_terms=OPIOID,
        title="Sedative + opioid - dangerous sedation",
        explanation="This combination carries an FDA boxed warning: together they can "
        "slow breathing to a dangerous degree, especially during sleep.",
        recommendation=f"This pairing needs explicit sign-off from one doctor who knows about both. {_DISCUSS}",
    ),
    InteractionRule(
        rule_id="levothyroxine-ppi",
        severity=FindingSeverity.moderate,
        category=FindingCategory.drug_drug,
        a_terms=("levothyroxine",),
        b_terms=PPI,
        title="Thyroid medicine + acid reducer - reduced absorption",
        explanation="Acid-reducing medicines can lower how much levothyroxine your body "
        "absorbs, which can quietly push thyroid levels off target.",
        recommendation=f"Worth a thyroid level check if this combination is ongoing. {_DISCUSS}",
    ),
    # --- drug + supplement/vitamin/mineral ------------------------------------
    InteractionRule(
        rule_id="warfarin-vitk",
        severity=FindingSeverity.major,
        category=FindingCategory.drug_supplement,
        a_terms=("warfarin",),
        b_terms=("vitamin k",),
        title="Warfarin + vitamin K - directly works against the blood thinner",
        explanation="Vitamin K is the antidote to warfarin: supplementing it (or big "
        "changes in intake) directly reduces warfarin's effect and can destabilize INR.",
        recommendation=f"Your warfarin clinic needs to know about any vitamin K supplement. {_DISCUSS}",
    ),
    InteractionRule(
        rule_id="anticoag-bleeding-supps",
        severity=FindingSeverity.moderate,
        category=FindingCategory.drug_supplement,
        a_terms=ANTICOAGULANT,
        b_terms=BLEEDING_RISK_SUPP,
        title="Blood thinner + supplement that also thins blood",
        explanation="Fish oil/omega-3, turmeric/curcumin, ginkgo, garlic supplements, and "
        "high-dose vitamin E each have mild blood-thinning effects that add to a "
        "prescription blood thinner.",
        recommendation=f"Mention this supplement at your next visit; dose and timing matter. {_DISCUSS}",
    ),
    InteractionRule(
        rule_id="sjw-ssri",
        severity=FindingSeverity.major,
        category=FindingCategory.drug_supplement,
        a_terms=SSRI,
        b_terms=ST_JOHNS_WORT,
        title="St. John's Wort + SSRI antidepressant - serotonin syndrome risk",
        explanation="St. John's Wort acts on serotonin the same way SSRIs do; the "
        "combination can cause serotonin syndrome and is generally advised against.",
        recommendation=f"Tell your prescriber before continuing this combination. {_DISCUSS}",
    ),
    InteractionRule(
        rule_id="sjw-interacting-drugs",
        severity=FindingSeverity.major,
        category=FindingCategory.drug_supplement,
        a_terms=("warfarin", "digoxin", "cyclosporine", "tacrolimus",
                 "ethinyl estradiol", "levonorgestrel", "norethindrone"),
        b_terms=ST_JOHNS_WORT,
        title="St. John's Wort - makes several medicines stop working properly",
        explanation="St. John's Wort speeds up how the liver clears many drugs "
        "(including warfarin, digoxin, transplant medicines, and hormonal birth "
        "control), which can make them lose effect.",
        recommendation=f"This is one of the most interaction-prone supplements there is. {_DISCUSS}",
    ),
    InteractionRule(
        rule_id="serotonergic-supp-ssri",
        severity=FindingSeverity.major,
        category=FindingCategory.drug_supplement,
        a_terms=SSRI,
        b_terms=SEROTONERGIC_SUPP,
        title="5-HTP/tryptophan + SSRI antidepressant - serotonin syndrome risk",
        explanation="5-HTP and L-tryptophan are serotonin building blocks; combined with "
        "an SSRI they can push serotonin too high.",
        recommendation=f"Generally advised against without medical supervision. {_DISCUSS}",
    ),
    InteractionRule(
        rule_id="levothyroxine-minerals",
        severity=FindingSeverity.moderate,
        category=FindingCategory.drug_supplement,
        a_terms=("levothyroxine",),
        b_terms=DIVALENT_MINERALS,
        title="Thyroid medicine + calcium/iron/magnesium/zinc - take hours apart",
        explanation="These minerals bind levothyroxine in the gut and block its "
        "absorption. This is a timing problem, not a 'never combine' problem.",
        recommendation="Take levothyroxine on an empty stomach and keep mineral "
        f"supplements at least 4 hours away from it. {_DISCUSS}",
    ),
    InteractionRule(
        rule_id="antibiotic-minerals",
        severity=FindingSeverity.moderate,
        category=FindingCategory.drug_supplement,
        a_terms=TETRA_QUINOLONE,
        b_terms=DIVALENT_MINERALS,
        title="Antibiotic + calcium/iron/magnesium/zinc - antibiotic may not absorb",
        explanation="These minerals latch onto quinolone and tetracycline antibiotics in "
        "the gut, so much less antibiotic gets into the body.",
        recommendation="Separate the antibiotic and the mineral by several hours (your "
        f"pharmacist can give exact timing for this antibiotic). {_DISCUSS}",
    ),
    InteractionRule(
        rule_id="ace-potassium-supp",
        severity=FindingSeverity.major,
        category=FindingCategory.drug_supplement,
        a_terms=ACE_ARB + POTASSIUM_SPARING,
        b_terms=("potassium",),
        title="Blood pressure medicine + potassium supplement - high potassium risk",
        explanation="These blood pressure medicines already make the body hold on to "
        "potassium; adding a potassium supplement can push levels dangerously high.",
        recommendation="Unless a doctor specifically prescribed the potassium alongside "
        f"this medicine, flag it promptly. {_DISCUSS}",
    ),
    InteractionRule(
        rule_id="sedative-melatonin",
        severity=FindingSeverity.info,
        category=FindingCategory.drug_supplement,
        a_terms=BENZO_SEDATIVE + OPIOID,
        b_terms=("melatonin", "valerian"),
        title="Sedating medicine + sleep supplement - additive drowsiness",
        explanation="Melatonin or valerian on top of a prescription sedative or opioid "
        "adds to drowsiness and next-day grogginess.",
        recommendation=f"Usually manageable, but worth mentioning - especially before driving. {_DISCUSS}",
    ),
    # --- supplement + supplement ----------------------------------------------
    InteractionRule(
        rule_id="calcium-iron",
        severity=FindingSeverity.info,
        category=FindingCategory.supplement_supplement,
        a_terms=("calcium",),
        b_terms=("iron",),
        title="Calcium + iron - absorb poorly together",
        explanation="Calcium competes with iron for absorption; taking them at the same "
        "time reduces how much iron you actually get.",
        recommendation="Simple fix: take them at different times of day (e.g., iron in "
        "the morning, calcium at night).",
    ),
)


DEPLETION_RULES: tuple[DepletionRule, ...] = (
    DepletionRule(
        rule_id="metformin-b12",
        drug_terms=("metformin",),
        drug_label="Metformin",
        nutrient="Vitamin B12",
        nutrient_terms=("b12", "b-12", "cobalamin", "methylcobalamin"),
        explanation="Long-term metformin use reduces vitamin B12 absorption; low B12 can "
        "cause fatigue and nerve symptoms that are easy to misattribute.",
        recommendation_if_missing="Ask your doctor about checking B12 levels and whether "
        "a B12 supplement makes sense for you.",
    ),
    DepletionRule(
        rule_id="ppi-magnesium-b12",
        drug_terms=PPI,
        drug_label="Acid reducer (PPI)",
        nutrient="Magnesium (and vitamin B12)",
        nutrient_terms=("magnesium",),
        explanation="Long-term acid reducers (omeprazole and similar) can lower magnesium "
        "and B12, since stomach acid is needed to absorb both.",
        recommendation_if_missing="If this medicine is long-term, ask about periodic "
        "magnesium/B12 checks and whether supplementing is worthwhile.",
    ),
    DepletionRule(
        rule_id="statin-coq10",
        drug_terms=STATIN,
        drug_label="Statin (cholesterol medicine)",
        nutrient="Coenzyme Q10",
        nutrient_terms=("coq10", "coenzyme q10", "ubiquinol"),
        explanation="Statins lower the body's CoQ10 production. Evidence that "
        "supplementing helps with statin muscle aches is mixed, but the depletion "
        "itself is well documented.",
        recommendation_if_missing="If you have muscle aches on a statin, CoQ10 is a "
        "reasonable thing to raise with your doctor (evidence is mixed, risk is low).",
    ),
    DepletionRule(
        rule_id="loop-diuretic-electrolytes",
        drug_terms=DIURETIC_LOOP,
        drug_label="Loop diuretic (water pill)",
        nutrient="Potassium, magnesium, and thiamine (B1)",
        nutrient_terms=("potassium", "magnesium", "thiamine"),
        explanation="Loop diuretics flush out potassium, magnesium, and thiamine along "
        "with fluid.",
        recommendation_if_missing="Electrolyte monitoring usually accompanies this "
        "medicine - confirm it's happening and ask whether supplementation is needed.",
    ),
    DepletionRule(
        rule_id="thiazide-electrolytes",
        drug_terms=DIURETIC_THIAZIDE,
        drug_label="Thiazide diuretic (water pill)",
        nutrient="Potassium and magnesium",
        nutrient_terms=("potassium", "magnesium"),
        explanation="Thiazide diuretics lower potassium and magnesium over time.",
        recommendation_if_missing="Ask whether your routine labs include potassium and "
        "magnesium, and whether supplementing makes sense.",
    ),
    DepletionRule(
        rule_id="steroid-bone",
        drug_terms=CORTICOSTEROID,
        drug_label="Corticosteroid",
        nutrient="Calcium and vitamin D",
        nutrient_terms=("calcium", "vitamin d"),
        explanation="Ongoing corticosteroid use accelerates bone loss; calcium and "
        "vitamin D status matter more than usual while on it.",
        recommendation_if_missing="If this is more than a short course, ask about bone "
        "protection - calcium/vitamin D and sometimes a bone-density check.",
    ),
)


# Therapeutic classes for duplicate-therapy detection: two different active
# medicines from the same class, typically from different prescribers, is a
# classic uncoordinated-care failure worth surfacing.
DUPLICATE_CLASSES: dict[str, tuple[str, ...]] = {
    "NSAID pain relievers": NSAID,
    "statins (cholesterol)": STATIN,
    "ACE inhibitors / ARBs (blood pressure)": ACE_ARB,
    "SSRI antidepressants": SSRI,
    "acid reducers (PPIs)": PPI,
    "benzodiazepines / sleep sedatives": BENZO_SEDATIVE,
    "opioid pain medicines": OPIOID,
}
