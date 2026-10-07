# Golden test results — 7 October 2026

| Suite | Cases | Before fixes | **After fixes** | Workbook (Result / Root cause columns) |
|---|---:|---:|---:|---|
| Fixture (fictional GC-1 catalog) | 104 | 80 | **104** | `docs/GOLDEN_RECOMMENDATION_TESTS.xlsx` |
| Production (58 verified outlets, now `0.4.0-translated`, live Chroma index, multilingual MiniLM) | 107 | 37 | **99** | `docs/GOLDEN_PRODUCTION_58_TESTS.xlsx` |

The golden tests were **not changed** for the fixes. Only application code changed. Raw
per-case output is in `var/golden-results/{fixture,production,summary}.json`.

```bash
DINING_GOLDEN=1 HF_HUB_OFFLINE=1 .venv/bin/python -m pytest tests/golden -q   # both golden suites
.venv/bin/python -m pytest tests -q                                           # normal run (golden suites skipped)
```

## Update — majority cases added, production suite repointed (7 Oct 2026, evening)

- **Production suite now runs on `0.4.0-translated`** (product-owner approved): `CATALOG_DIR` in `tests/golden/test_golden_production.py`. The outside plugin is no longer needed.
- **11 majority-preference cases added** (product-owner approved): fixture GT-101–GT-104 and production P-101–P-107. All pass. They cover two of three by cuisine and by dish, three of four, the majority flipping a compromise, and the majority **not** overriding the fairness floor (GT-104) or a hard requirement (P-107).
- Results: **fixture 104/104, production 99/107** (the same 8 known tagging or expectation cases).
- Specs and workbooks were updated: `docs/GOLDEN_RECOMMENDATION_TESTS.md/.xlsx`, `docs/GOLDEN_PRODUCTION_58_TESTS.md/.xlsx`.

## Update — ILMU fixed, production switched, majority check (7 Oct 2026, late afternoon)

- **ILMU explanation labels fixed.** `ilmu-mini-v3.3` answered prompt `allowed-reasons-v2` with one string per option instead of two labels, so every reply was rejected. Prompt **`allowed-reasons-v3`** (`dining/inference.py`) states the array shape explicitly; validation is unchanged. Live result: 3/3 validated on P-039 and 4/4 on the Malay-majority runs, at 193/87 tokens, about US$0.00008 per meal.
- **Production switched to `0.4.0-translated`.** The live index `var/vector/catalog` was rebuilt for it, and the previous index is backed up in `var/backups/vector-catalog-before-translated-20261007-132943`. The README launch command was updated.
- **Coverage corrected** (`rank_diverse`, `Recommender`). Slot 1 is again always the highest group score (PRD §9.3); coverage only breaks exact ties there. A diner's best match is now the eligible outlet with *their own* highest PRD fit, with search relevance only breaking ties. Before this, two Malay diners plus one Turkish or Chinese diner put Grand Hisar or Hakka first.
- **Malay majority (not in the golden suites; checked ad hoc):** Malay + Malay + {Turkish, Japanese, Chinese, Korean} at KLCC → **Cili Kampung first in all four**, and the third person's cuisine second where it exists.
- Golden on the production catalog and live index: **fixture 100/100, production 92/100** (same 8 known cases). Normal suite: 412 passed, the same 9 unrelated failures.

## Update — dish-name translations applied (7 Oct 2026, afternoon)

- 969 high-confidence translations (product-owner approved) applied to catalog **`0.4.0-translated`** (`var/catalog-import/kl-selangor-real-pilot-58-translated/`). The 104 low-confidence entries were not applied, because the validator blocks approving them; those dishes have no translations.
- Code: `MenuItem.name_translations` added (the schema contract `schemas/restaurant-catalog.schema.json` was regenerated; only this field was added). The hard-coded `DISH_WORDS` table was **removed**. Matching (`name_term_overlap` via `item_search_text`) and the embedded text now use each dish's reviewed translations.
- Shortlist coverage now also applies to the first slot (`rank_diverse`). This fixed P-031, where a restaurant that was nobody's best match took slot 1.
- Index built into a **staging** directory, `var/vector/catalog-staging-translated`. The live index `var/vector/catalog` is still `0.3.0-tagged` and needs a deliberate switch.
- Golden results on the translated catalog: **fixture 100/100, production 92/100**. The remaining 8 are the known tagging or expectation cases (P-019, 024, 026, 037, 064, 065, 089, 091).
  The production suite was pointed at the translated catalog and staging index with a pytest plugin outside the repo, without editing `tests/golden/`.
- Normal suite: 411 passed, 9 failed, the same unrelated 9 as before (7 × `test_verified_58_vector.py` version pins, 2 × the meal-creation 422).
- Live ILMU run of P-039 through `DiningAgent`: 1 call, 155/80 tokens, about US$0.000071. ILMU's reply **failed validation** (`invalid_output`), so template reasons were used and the shortlist was unaffected. See the flow diagram artifact.

## What was fixed

| Fix | Files | Cases fixed |
|---|---|---|
| Add-ons are never a meal (`NON_MEAL_ROLES` now includes `add_on`; decision 2) | `dining/ranking.py`, `dining/recommendation.py` | P-003, 023, 026, 028, 042, 061, 071 |
| A dish with no price never clears a firm cap. Per-100 g / per-kg prices count as "no meal price" (decision 1). | `dining/recommendation.py`, `dining/personal_recommendations.py` | P-009, 039, 054, 060, 063, 064, 066, 067, 070, 085, 092 |
| Published weekly hours are enforced: a known-closed slot is excluded even without dated-exception coverage. If the weekly hours cover the meal, unconfirmed exceptions become a visible trade-off. | `dining/recommendation.py` (`_weekly_service`, `ServiceCheck.weekly_open`) | P-052, 072–080 |
| Retrieval is limited to the meal's search radius *before* the top-30 cut, and searches the whole index for that area. It uses every diner's craving (not just the first two). It adds a whole-word dish-name match to the score, and an empty area returns `no_options`. | `dining/retrieval.py`, `dining/recommendation.py` | P-006, 030, 034, 057, 087 and most relevance cases |
| Relevance beats distance (decision 3). Retrieval relevance breaks ties before distance, but only when someone actually expressed a taste ("anything" doesn't count). Confirmed mains/sets break the next tie, ahead of unknown-role items. | `dining/ranking.py` `rank_diverse`, `dining/recommendation.py` | P-001, 002, 007, 012, 019, 020, 027, 029, 054, 061, 092 … |
| Shortlist coverage: within the existing .10 margin, prefer an outlet that best serves a diner whose wish isn't covered yet | `dining/ranking.py`, `dining/recommendation.py` | P-031, 034, 042 |
| Local dish names map to the curated families: ramen/laksa/pho/curry mee → `noodle_soup`; biryani and nasi lemak → `rice`; tom yam → `soup`; "noodle soup" is one dish family | `dining/ranking.py` (`ALIASES`, `PHRASES`) | GT-043, P-007, 008, 016, 027, 030 |
| A curated English ↔ Chinese/Malay/Japanese dish-word table (e.g. pho ↔ 河粉, char siu ↔ 叉燒, sushi ↔ 寿司), used **only to break ties** between otherwise equal dishes or outlets | `dining/ranking.py` (`DISH_WORDS`, `name_term_overlap`) | P-003, 016, 020, 082, 083, 090 |
| Known conflicts are excluded instead of sent to "Needs confirmation": a firm cap that rules out every dish, complete ingredients naming meat for a vegetarian, and a known-inaccessible entrance | `dining/recommendation.py`, `dining/catalog_audit.py` (`mentions_animal`) | GT-008, 017, 028, P-062 |
| "Flexible" budget (a comfortable target, no firm cap) no longer blocks the meal | `dining/recommendation.py` | GT-048 |
| **Personal scorer rewritten (MAKAN-212):** the dish must pass the diner's own diet, avoid list, cap (with a known price), meal role, review and availability checks. The score is the diner's PRD individual fit. Ties go to the outlet that answers the diner's own wish, then a confirmed main, then a whole-word name match, then price. Fuzzy `difflib` matching is removed. Reason codes are accurate. Version `personal-fit-v2`. | `dining/personal_recommendations.py` | GT-076–093, P-013–015, 022, 033, 037, 040, 081, 083, 086, 090, 098 |
| Personal results are only shown for the meal's current revision; no fallback to older revisions | `dining/personal_recommendations.py` | GT-098 |

## The 9 production cases still failing (none are code defects — please decide)

| Case | Why | Suggested action |
|---|---|---|
| P-019, P-024, P-037 | The tagging session marked sushi pieces, yong tau foo and naan as **side**. Under decision 2, a side is never a meal, so they can't be someone's personal dish. | Re-tag these as `main` if you consider them meals (`scripts/taste_tags.py` patch → apply → re-index), or accept the result |
| P-026 | Expected a list of Arabic mains; the result was Falafel Sandwich, which is also Arabic | Widen the expectation |
| P-036 | Expected De Forest for "chicken chop"; Hee Lai Ton also sells chicken chops (柠檬鸡扒) | Widen the expectation |
| P-064 | The correct dish was chosen (Devesa … Tenderloin **220g**), but the test checks only the dish name, and "220g" is in the variant | Make the test check name + variant |
| P-065, P-089 | Green View's sang har noodle has **no price**, so under decision 1 it can't clear the cap. AROI's priced noodle dish is then the PRD best fit. | Change the expectation to "Green View's sang har noodle is shown only with 'price needs confirmation'" |
| P-091 | My expectation error: P2 has the default RM60 cap, and PRIME has no meal under RM60, so PRIME can't serve the group | Fix the case: give P2 a larger cap, or expect PRIME to be excluded |

You asked me not to change the golden tests, so these remain as written.

## Corrections made earlier to my own expectations (before the fixes)

GT-019 (fixture schema), GT-060/061 (oracle omitted learned venue liking), production P-015, 023, 031, 037 and 086 (aligned to decision 2), and production renumbering. Details are in the git diff of the two spec docs.

## Existing suite

`pytest tests`: 394 passed, 9 failed, 204 skipped (golden). These are **the same 9 failures as before the fixes**, so there are no regressions:

- 7 in `tests/test_verified_58_vector.py` pin catalog `0.2.0-reviewed`, while the active index is now `0.3.0-tagged` (switched by the tagging session). They need repointing to the tagged catalog. Until then they don't exercise the new retrieval code.
- `test_authorization_matrix::test_cross_room_and_outsider_access_refused` and `test_geocoding::test_provider_failure_does_not_block_meal_creation`: meal creation returns 422. `dining/api.py` was changed at 10:18 today, before this work, and was not touched here.

## Behaviour changes to be aware of

- Retrieval now runs one extra search per diner (for coverage) and scans the full index within the area. For 58 outlets and 1,073 items this added well under a second per generation in the suite run.
- `semantic_candidate_search` has a new optional `outlet_ids` argument. Existing callers are unchanged.
- Personal results from older revisions are no longer shown. After an edit, a diner sees their picks again once the meal is regenerated.
- The dish-word table and aliases are curated data. Extend them through review, the same way as the taste tags.
