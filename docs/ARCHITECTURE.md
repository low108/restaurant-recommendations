# Architecture

This is a map of the `dining` package: where each responsibility lives and how a request
flows through it. For product rules, see the PRD. For retrieval details, see
`VECTOR_RETRIEVAL.md`. For the content-similarity policy, see `docs/CONTENT_SIMILARITY.md`.

## Package layout

The code is grouped by domain. Each folder's `__init__.py` says what it owns.

```
dining/
├── core/             infrastructure every domain uses
│   ├── constants.py      shared constants and vocabularies (meal states, time units, tags…)
│   ├── store.py          SQLite database (DiningStore), JSON encode/decode
│   └── runtime_env.py    .env loading
├── catalog/          restaurant data
│   ├── models.py         validated catalog models (Catalog, Outlet, MenuItem, Price…)
│   ├── audit.py          catalog quality and dietary-claim checks
│   ├── manifest.py       activation manifests (approved 58-outlet scope)
│   └── cli.py            python -m dining.catalog.cli: validate / upgrade / install
├── retrieval/        semantic candidate search
│   ├── index.py          embedders, in-memory and Chroma indexes, hybrid search
│   └── evaluation.py     multilingual retrieval judgement cases
├── recommendation/   deciding what to suggest
│   ├── agent.py          DiningAgent (LangGraph: check_and_rank → explain_supported_options)
│   ├── engine.py         Recommender: hard checks, evidence, per-outlet assessment
│   ├── service_hours.py  opening hours and last-order checks
│   ├── ranking.py        PRD §9 feature model and diverse shortlist
│   ├── scoring_policies.py   ScoringPolicy strategy (PRD fit / content similarity)
│   ├── content_similarity.py TF-IDF content features
│   ├── personal.py       private "best fit for you" picks (MAKAN-212)
│   ├── adaptive.py       optional follow-up question (M09)
│   └── metrics.py        P@k, R@k, NDCG@k, MRR, Top-1
├── llm/              optional language-model features
│   ├── inference.py      provider settings and explanation-label calls
│   ├── preferences.py    craving text → suggested tags
│   ├── pricing.py · tracing.py   token cost and redacted tracing
│   ├── evaluation.py     python -m dining.llm.evaluation (ILMU quality harness)
│   └── check.py          python -m dining.llm.check (configuration check)
├── meals/            meal workflow state
│   ├── generation.py     GenerationWorker: queued jobs, leases, retries
│   ├── lifecycle.py      post-meal state transitions
│   ├── exposure.py · outcomes.py   exposure events and outcome metrics
│   └── learning.py       attribute feedback signals and proposals
├── accounts/         email.py (verification, password reset) · consent.py
├── notifications/    reminders.py (in-app reminders) · push.py · push_worker.py
├── location/         routing.py · geocoding.py (interfaces + disabled and fake providers)
└── api/              HTTP layer
    ├── __init__.py       build_router() composition root, patchable dining.api.now
    ├── common.py         clock, ids, hashing, session settings
    ├── schemas.py        request bodies (pydantic, extra fields forbidden)
    ├── context.py        ApiContext: auth, membership, lifecycle, invalidation, meal view
    ├── generation.py     GenerationPipeline: snapshot → recommend → publish
    ├── operations.py     token-protected operations routes
    └── routes/           accounts · rooms · meals · adaptive · decisions · learning
```

Dependencies: `core` imports nothing else; `catalog`, `accounts` and `location` depend
only on `core`; nothing imports `api`, the HTTP layer at the top. Two cycles remain:
`retrieval` and `llm` use the dish-text helpers in `recommendation/ranking.py`, while
`recommendation` calls both of them. Moving those helpers into `catalog` would remove
the cycles.

## How one recommendation is made

1. **`POST /api/meals/{id}/generate`** (`api/routes/decisions.py`): the organizer asks for a shortlist.
2. **`GenerationPipeline.snapshot_for`** freezes each participant's profile and response.
   **`safe_recommend`** stops the run if any private requirement is unknown or unreviewed.
3. **`GenerationWorker`** (`meals/generation.py`) runs the job, either inline or on a background
   thread. It uses leases, timeouts and up to 3 attempts. If the meal changed meanwhile,
   **`prepare_generation`** raises `Superseded`.
4. **`DiningAgent`** runs the LangGraph:
   - **`check_and_rank`** calls `Recommender`:
     1. **Readiness gate:** every requirement must have been reviewed.
     2. **`_find_candidates`:** `semantic_candidate_search` limited to the search area.
     3. **`_assess_outlet`** for each candidate checks:
        - that the evidence is in scope;
        - hours;
        - travel;
        - each diner's dish eligibility (diet, avoid list, firm budget, a real meal);
        - price.
     4. **`rank_diverse`** orders the survivors using the active `ScoringPolicy`:
        - individual fit = .45 craving + .20 usual taste + .15 travel + .10 budget + .10 occasion
        - group fit = .6 mean + .4 minimum
        - Diversity rules prevent near-duplicate options.
   - **`explain_supported_options`:** when a model is configured, it may choose two reason
     labels per option from an allowlist. Otherwise the deterministic reasons are used.
5. **`publish_generation`** stores the result and each diner's private picks
   (`personal_recommendations.py`), records exposure, and notifies participants. All of this
   happens in one transaction.

## Design rules that the structure protects

| Rule | Where it is enforced |
|---|---|
| The model never decides eligibility or authorization | `recommendation/agent.py` passes only option ids and allowed reasons; `llm/inference.py` validates the reply |
| Unknown evidence never passes a firm check | `Recommender._dish_verdict` / `_price_verdict` return `"unknown"`; `service_hours.service_for` returns `None` |
| Private requirements stay private | `ApiContext.meal_view` returns only the caller's own data; `safe_recommend` keeps only public result fields; internal scores (`PRIVATE_OPTION_KEYS`) are stripped from options |
| Search finds candidates, it never filters | `retrieval/index.py` only orders outlets; all checks happen in `recommendation/engine.py` |
| Every change makes a new revision | `ApiContext.invalidate` bumps the revision and clears the shortlist, votes and decisions |
| Swappable scoring | `ScoringPolicy` protocol + `create_scoring_policy` (Strategy pattern) |
| Swappable external providers | `RoutingProvider`, `GeocodingProvider` interfaces with disabled and fake implementations |

## Extending

- **New route group:** add `dining/api/routes/<name>.py` with `register(router, ctx)`, then list
  it in `api/routes/__init__.py`. Bind the `ApiContext` helpers you need at the top of
  `register`.
- **New scoring policy:** implement `ScoringPolicy.score(...)` and register it in
  `create_scoring_policy`. Measure it with `scripts/evaluate_ranking.py`.
- **New hard requirement:** add the check in `Recommender._dish_verdict` (per dish) or
  `_outlet_issues` (per outlet), then add a golden case in `tests/golden/`.
- **New shared value:** if two modules must agree on a value (a status set, a vocabulary,
  a time unit), put it in `dining/core/constants.py`. Values used by one module stay local.

## Tests

| Suite | Command |
|---|---|
| Unit and API | `.venv/bin/python -m pytest tests --ignore=tests/browser --ignore=tests/golden` |
| Golden recommendation suites | `DINING_GOLDEN=1 HF_HUB_OFFLINE=1 .venv/bin/python -m pytest tests/golden` |
| Ranking metrics (P@3, R@3, NDCG@3, MRR, Top-1) | `HF_HUB_OFFLINE=1 .venv/bin/python scripts/evaluate_ranking.py` |

Tests freeze time with `monkeypatch.setattr("dining.api.now", ...)`. Every API module reads
the clock through `dining.api.common.now()`, which looks up `dining.api.now` when it is called, so
the freeze reaches every route.
