# Content-based similarity (`content-sim-v1`) and ranking evaluation

**Date:** 7 October 2026 · **Status:** implemented as an opt-in policy; production still uses `prd-fit-v1`.

## Why

The production ranker (`prd-fit-v1`) scores taste with fixed match levels: exact 1.0, related 0.7,
unknown 0.5, mismatch 0.2 (PRD §9.2). That is rule-based feature matching, closer to knowledge-based
scoring, not a similarity metric. Embedding cosine is used only to retrieve candidates and break ties.
No offline ranking metrics were computed.

## What was built

| Piece | Where | What it does |
|---|---|---|
| Similarity policy | `dining/content_similarity.py` | Builds **TF-IDF feature vectors** for every reviewed dish: cuisine (from the dish or its outlet), curated taste tags, and words from the reviewed English/Malay translations, with IDF over the catalog. Builds a **taste vector for today** (craving tags and words, cuisines, appetite) and a **lasting vector** (saved likes +, dislikes −, and consented dish-level history added or subtracted Rocchio-style, with a 90-day half-life). |
| Scoring | same | Craving: **C = 0.2 + 0.8 × cos(today, dish)**. Usual taste: **H = 0.5 + 0.5 × cos(lasting, dish)**, signed, so dislikes pull it below neutral. Unknown inputs stay neutral at 0.5 with no coverage. Travel, budget, occasion, hard checks, group aggregation (.6 mean + .4 min), the .35 floor and diversity are unchanged. |
| Switch | `Recommender(..., scoring_policy="content-sim-v1")` | Default stays `prd-fit-v1`. The policy is recorded in `result.policy_version` and in option IDs. |
| Metrics | `dining/eval_metrics.py` | Precision@k, Recall@k (capped at k, because a shortlist holds 3), NDCG@k with graded relevance, MRR, and top-1 hit. Undefined metrics, for example NDCG with no relevant outlets, are reported separately, never as 0 (PRD §15.3). |
| Evaluation | `scripts/evaluate_ranking.py` | Reuses the production golden scenarios. Labels come from their expectations: an outlet required at #1 is grade 2, a required or allowed outlet grade 1, and in a "must include" set for a mixed group each outlet is grade 2. It compares four policies **on the same eligible pool**. Output goes to `var/eval/ranking-eval.json` and the labels to `data/eval/production-relevance.json`. |
| Tests | `tests/test_eval_metrics.py`, `tests/test_content_similarity.py` | 13 unit tests for the metric definitions, cosine/IDF behaviour, neutral and negated cravings, dislikes, consent-gated history, and the default policy being unchanged. |

## Results (46 labelled production scenarios, catalog 0.4.0-translated)

| Policy | P@3 | R@3 | NDCG@3 | MRR | Top-1 |
|---|---:|---:|---:|---:|---:|
| Nearest eligible | 0.377 | 0.754 | 0.596 | 0.554 | 0.348 |
| Average fit only | 0.435 | 0.866 | 0.730 | 0.714 | 0.543 |
| **PRD blend (`prd-fit-v1`, production)** | **0.493** | **0.975** | **0.977** | **1.000** | **0.957** |
| Content similarity (`content-sim-v1`) | 0.485 | 0.967 | 0.940 | 0.949 | 0.891 |

P@3 can't reach 1.0 here, because most scenarios have only one or two relevant outlets.

**Where content-sim differs (8 scenarios).**
- **Better on 2:** P-035 and P-042. Graded similarity ranks the outlet that matches each person's craving above a closer generic one.
- **Worse on 6:**
  - P-026, P-028, P-030 ("Arabic food", "hot pot", "biryani"): the words aren't dish features, so the cosine gives only partial credit. Scores no longer tie, so the menu search, which gets these right, never breaks the tie.
  - P-106 and P-037: AROI and Bean Jr have **no cuisine tag**, so "Thai" can't match them.
  - P-031: Shibuya's sandwiches carry no dessert-related features.

### Read these numbers with care
- **The labels favour the PRD blend.** They come from golden expectations that the PRD ranker was fixed against today. A fair comparison needs independently reviewed labels (PRD §15.3).
- **46 scenarios** is under the PRD's target of at least 50, and all come from one catalog snapshot. That's enough to describe behaviour, not to claim significance.
- Content-sim's losses line up with **missing item features**, not with the metric itself. The next section is what would close that gap.

## How the dataset would need to change for content-based similarity to work well

### 1. Restaurant and dish features (catalog)
| Gap today | Change |
|---|---|
| 40 of 58 outlets, and 239 of 371 mains, have **no cuisine** | Cuisine for every outlet, and for every dish where it differs from its outlet |
| 107 mains have **no taste tag**; the ontology lacks common families (`proposed_new_tags`: sushi 25, curry 16, ramen 12, dumpling 7, kebab 5, burger 5, wrap 4) | Extend the ontology (versioned, e.g. `dining-tags-v2`) and tag every main |
| No structured description of what a dish *is* | Add namespaced descriptive features, e.g. `base:{rice,noodle,bread}`, `protein:{chicken,beef,pork,seafood,tofu,egg}`, `method:{fried,grilled,steamed,soup,curry}`. These are descriptive only and must **never** act as diet or halal claims. |
| IDF is computed in memory per catalog | Store the feature vocabulary and IDF table as a versioned derived file next to the catalog and index, so scores are reproducible and auditable |

A schema change is needed for the descriptive features, either a new `MenuItem.features` field or namespaced `attributes`. It would go through the same reviewed patch → apply → re-index workflow as the taste tags and translations.

### 2. People's taste data
| Gap today | Change |
|---|---|
| Visit history is per **restaurant**, with an optional free-text dish (`dish_text`) | After-meal feedback (F06) records the **`item_id`** of the dish eaten, chosen from the menu, so history can add or subtract that dish's vector |
| Saved preferences are a few cuisine/tag likes and dislikes | No change needed. They become positive or negative weights in the lasting vector. |
| — | Nothing new needs storing: the taste vector is computed when needed from consented data, so deleting an observation removes its influence (as today) |

### 3. Evaluation data
| Gap today | Change |
|---|---|
| Labels are derived from golden expectations | An independently reviewed, graded relevance set of at least 50 scenarios, versioned in `data/eval/` |
| No real outcomes | Once the pilot runs: labels from accepted options and "enjoyed" feedback, evaluated with time-ordered splits (PRD §15.3) |

## How to run

```bash
HF_HUB_OFFLINE=1 .venv/bin/python scripts/evaluate_ranking.py
.venv/bin/python -m pytest tests/test_eval_metrics.py tests/test_content_similarity.py -q
```
