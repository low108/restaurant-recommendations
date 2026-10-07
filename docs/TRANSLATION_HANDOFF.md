# Handoff — dish-name translations, done in a Claude Sonnet session

**Created:** 7 October 2026
**Decision (product owner, 7 Oct 2026):** do not use ILMU or any other model API for this.
The working session (Claude Sonnet) translates the dish names itself, the same way the taste
tags were produced (`docs/TASTE_TAGS_HANDOFF.md`). Ask the product owner for approvals; this
document grants none.

## Why

During the golden-test fixes, a hand-written synonym table (`DISH_WORDS`) was added to make
English cravings match Chinese- and Malay-named dishes (e.g. "beef pho" ↔ `招牌石鍋牛肉河粉`).
The product owner does not want hard-coded translations in code. Replace the table with
per-dish translations stored as reviewable catalog data.

## What "translate the dish names" means here

- For each of the **1,073 reviewed dishes** at the 58 verified outlets, add English, Malay and
  Chinese names and common dish words as **extra searchable data next to the dish**.
- The original `name`, `variant` and `description` are **never changed**. Users keep seeing the
  restaurant's own menu text.
- Translations only help search and tie-breaks. They never decide diet, allergy, halal, budget,
  hours or any other eligibility.

Example for one dish:

```json
{
  "item_id": "scan-viet-pho-cafe-menu-ec6e18-001-p1-regular",
  "translations": {
    "en": ["signature stone pot beef pho", "beef pho", "beef rice noodle soup"],
    "ms": ["pho daging", "mee sup daging"],
    "zh": ["招牌石鍋牛肉河粉", "牛肉河粉"]
  },
  "evidence": ["name"],
  "confidence": "high",
  "review": {"status": "needs_review", "reviewer": null},
  "_context": {"outlet": "Viet Pho Cafe", "name": "P1 招牌石鍋牛肉河粉", "variant": "Regular", "description": ""}
}
```

## Current state (what you are replacing)

| What | Where |
|---|---|
| `DISH_WORDS`: 29 hand-written groups (`{"pho", "河粉"}`, `{"chicken", "雞", "鸡", "ayam"}` …) | `dining/ranking.py` |
| `name_term_overlap(query, name)`: share of craving words found in a dish name. Latin words match whole words, CJK matches as substrings, negated phrases are dropped | `dining/ranking.py` |
| Used by `craving_name_overlap` (dish tie-break in the group ranker and personal scorer), and as `name_match` in `semantic_candidate_search` (30% of the outlet `recall_score`) | `dining/recommendation.py`, `dining/personal_recommendations.py`, `dining/retrieval.py` |
| Leave alone: `ALIASES` / `PHRASES` in `dining/ranking.py`. They map local dish names to the curated ontology (ramen/laksa/pho → `noodle_soup` …). That is ontology mapping, not translation. | `dining/ranking.py` |

**Golden baseline to protect:** fixture 100/100, production 91/100 (`docs/GOLDEN_TEST_RESULTS.md`).
Cases that rely on `DISH_WORDS` today: P-003 (拉面), P-016 (pho ↔ 河粉), P-020 (Hokkien mee ↔ 福建面),
P-028 (hot pot ↔ 鸳鸯/汤底), P-029 (chicken chop ↔ 雞扒), P-082/083 (Chinese-only Mi House and Man Kee dishes).

## Paths

| What | Path |
|---|---|
| Source catalog (read-only) | `var/catalog-import/kl-selangor-real-pilot-58-tagged/catalog.validated.json` (0.3.0-tagged) |
| Activation manifest | `var/catalog-import/kl-selangor-real-pilot-58-tagged/activation-manifest.json` |
| Patch to fill | `data/enrichment/dish-translations.patch.json` |
| Output catalog (only after approval) | a new directory, e.g. `var/catalog-import/kl-selangor-real-pilot-58-translated/` (version `0.4.0-translated`) |
| Pattern to copy | `scripts/taste_tags.py` (export → validate → stats → apply, receipt, no overwrite) |

## Steps

1. **Tooling.** Write `scripts/dish_translations.py` by copying the structure of `scripts/taste_tags.py`:
   - `export`: one entry per in-scope reviewed item, with read-only `_context`. The quarantined item and the 70 unverified outlets are excluded.
   - `validate`: rejects unknown or out-of-scope `item_id`s; languages other than `en` / `ms` / `zh`; empty or overlong terms (e.g. > 60 characters, > 6 terms per language); URLs or prose; requirement or diet claims ("halal", "vegetarian", "vegan", "no pork", "gluten-free" …); missing `evidence`; and approving a `low` entry.
   - `stats`: coverage per language and per outlet, plus a count of items left empty.
   - `apply`: writes a **new** catalog version and a `dish-translations.apply-receipt.json` (SHA-256 of input, patch and output). It refuses to overwrite.
2. **Translate.** Go outlet by outlet and fill `translations` yourself, reading each name, variant and description.
   - **Add, never rewrite:** keep the original wording as one of the terms in its own language.
   - **Generic dish words help matching:** e.g. "fried rice", "noodle soup", "nasi goreng", "炒饭".
   - **Only translate what the text says.** Don't infer ingredients, spice or portion that aren't written.
   - **Menu codes** like "R0014" or "SD.163" are not words. Drop them from translations.
   - **When unsure, leave it out.** Set `confidence: low`, or leave the language empty with a short `notes`.
3. **Validate and stats** until there are 0 errors.
4. **Review.** Show the product owner the `stats` output and the `low` / empty entries. Ask how they want to review before anything is set to `approved` or applied.
5. **Schema and runtime** (code changes, after approval of the data):
   - `dining/catalog.py`: add an optional field to `MenuItem`, e.g. `name_translations: dict[str, tuple[str, ...]] = {}`. The model uses `extra="forbid"`, so this is required. Add a contract test.
   - `dining/ranking.py`: make `name_term_overlap` / `craving_name_overlap` compare the craving against the dish name **plus its approved translations**, then **delete `DISH_WORDS`**. Keep: whole-word matching for Latin, substring matching for CJK, negated phrases dropped, tie-break only.
   - `dining/retrieval.py`: use the item's translations in the `name_match` signal. Append them in `build_embed_text`, so the embeddings see all three languages.
6. **Apply and re-index.** Apply to the new catalog version, then run `scripts/build_catalog_vector_index.py --dry-run`. Running with `--rebuild` switches the **live** index; ask the product owner first.

## Guardrails

- No model API calls (ILMU, OpenAI or others). The translations are written by the session.
- Don't edit `tests/golden/`. The product owner asked for golden tests to stay unchanged.
- Don't touch the source catalog, the live index (without approval), or `tests/conftest.py`'s inference isolation.
- Translations never change eligibility. Add a test showing that results with and without translations differ only in ordering among equally eligible options.
- Menu text comes from sites like scanmenu.my. The catalog records `display` / `embed` rights only. Mention this to the product owner when you report back.
- Don't commit unless asked.

## Tests

```bash
.venv/bin/python scripts/dish_translations.py validate   # 0 errors
.venv/bin/python -m pytest tests -q                       # normal suite (golden skipped)
DINING_GOLDEN=1 HF_HUB_OFFLINE=1 .venv/bin/python -m pytest tests/golden -q
.venv/bin/python -m ruff check dining tests scripts
```

The golden production suite reads `var/catalog-import/kl-selangor-real-pilot-58-tagged/`. To
measure the translated catalog without editing golden tests, report results both before and after
re-indexing. Ask the product owner whether the golden suite should be pointed at the new
catalog version; that is a test change, so it needs their decision.

Add tests for the script (validate rejections, receipt, no overwrite), the schema field, and the
runtime (translations used in matching; `DISH_WORDS` gone; eligibility unchanged).

## Done when

- Every in-scope dish has validated translations, or is listed as left empty with a reason.
- `DISH_WORDS` is gone from `dining/`, and matching uses per-dish translations instead.
- Golden: fixture stays 100/100; production is not below 91/100 against the catalog it runs on, and any change is explained case by case.
- The normal suite shows no new failures compared with `docs/GOLDEN_TEST_RESULTS.md`.
- `docs/GOLDEN_TEST_RESULTS.md` and this handoff are updated with what was done.
