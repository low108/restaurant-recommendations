# Makan Together — group dining web pilot

Mobile-friendly group dining for friends and family in Kuala Lumpur and Selangor.
The new interface uses FastAPI and static HTML/CSS/JavaScript, with the existing
Python/LangGraph agent stack, with optional ILMU or Ollama inference. SQLite stores accounts, private preferences,
rooms, meal check-ins, votes, decisions and feedback. Restaurant data stays in a
separately imported JSON catalog. The original Gradio/course demo remains below.

The interface follows a minimal retail style: white surfaces, black controls, a red
Makan wordmark, original line illustrations and fixed bottom navigation on mobile.
It uses system fonts and local assets, with no external design dependencies.

The current build adds durable recommendation jobs with restart recovery, explicit
holiday/kitchen checks, and repeatable browser tests. It also includes invitee
selection for larger rooms, conflict-safe personal
check-ins, three-state private voting, revocable delegation, manual unverified
plans, explicit reconfirmation/deadline states, richer feedback and reviewable
venue preferences. Interface copy features comprehensive Bahasa Melayu coverage
via an in-app language switch. See the [PRD acceptance matrix](docs/PRD_ACCEPTANCE.md)
for remaining requirements and the [E2E guide](docs/E2E.md) for test scope.

## Run the web app

Requires Python 3.12. From this checkout:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-web.txt
python webapp.py --demo
```

Open `http://127.0.0.1:7860`. `--demo` explicitly opts into fictional restaurants:
the interface labels them throughout. Create two test accounts in separate browser
profiles, review each profile, create a table, share its invite token, and start a
meal. Each person completes their own private check-in. The organizer generates
options; everyone approves an option before the organizer confirms it.

Without `--demo` or `--catalog`, the app starts with an empty catalog and explains
that no imported options are available. No scraper is included or run.

The default agent runs deterministic checks and a LangGraph flow without an LLM.
The requested ILMU model, `ilmu-mini-v3.3`, can now select approved explanation
labels after Python ranks the options. Set a server-only key in `var/inference.env`
using [the configuration example](config/inference.env.example), then run
`python webapp.py --demo --env-file var/inference.env`. The home screen shows
configuration; each meal shows whether a model actually ran or templates were used.
The adapter and optional, consented taste-draft flow are implemented. Two synthetic
ILMU contract probes passed on 6 October 2026; real recommendation quality remains
unevaluated.

After at least two diners submit ready check-ins, each diner can request one
optional M09 follow-up. The server performs bounded, deterministic counterfactual
ranking and asks only when the offered cuisine choices produce different
preliminary orders. The question contains no restaurant names or other diners'
answers, uses no model call, and Skip leaves the saved check-in unchanged.

Confirmed meals now follow a persisted post-meal lifecycle. At the planned finish
they move from `selected` or `manual_selected` to `awaiting_feedback`; seven days
later they become `closed`. API reads and the notification worker apply the same
rule. The transition keeps the decision, shortlist and private feedback history,
while a closed meal rejects new feedback.

For shortlist explanations, the model receives only opaque option IDs and allowed
reason IDs. For an optional taste draft, it receives only craving text explicitly
approved by that diner plus fixed allowlists; the editable proposal has no effect
until the diner submits the ordinary check-in. It never ranks venues. One bounded
request is allowed; failed or invalid output leaves the normal form and template
explanations usable. [Inference setup and test scope](docs/INFERENCE.md)
includes a no-network configuration check and an explicit synthetic live probe.
`python webapp.py --demo --ollama` retains the optional local model path, including
`OLLAMA_MODEL` and `OLLAMA_BASE_URL`.

## Collect and import restaurant data separately

- [Data-only session prompt](docs/SCRAPING_HANDOFF.md)
- [JSON schema](schemas/restaurant-catalog.schema.json)
- [Single-outlet fictional example](data/catalog.single-example.json)
- [Complete fictional catalog](data/catalog.example.json)

The collecting session exports `catalog.real.json`; it must not change the app,
write its database or build embeddings. Validate and install its delivery:

```bash
python -m dining.catalog_cli /path/to/catalog.real.json
python -m dining.catalog_cli /path/to/catalog.real.json --upgrade --install var/catalog.real.v2.json --report var/catalog.review.json
python webapp.py --catalog var/catalog.real.v2.json
```

Schema v2 adds meal categories, servings, price units/minimum quantities and review
states. V1 files still load; omitted fields stay unknown. `--upgrade` preserves
source assertions and flags obvious contradictory dietary text for review. It does
not approve sources, classify meals, verify channels or manufacture expiry dates.
The [v2 integration notes](docs/CATALOG_V2.md) describe the current collected snapshot.
The home screen and meal results show aggregate coverage and outstanding checks;
failed coverage requests do not block accounts, tables or private check-ins.

Restart to activate a changed catalog. Catalog content participates in option IDs,
so old selections cannot pass revalidation against a changed catalog even if a
collector reuses the version label. JSON validity is not source verification:
unknown permissions, unknown freshness, incomplete charges and missing dietary
evidence remain blockers. Recheck dates are app policy, not publisher guarantees.

## Implemented boundaries

- Passwords use Argon2; sessions use opaque HttpOnly/SameSite cookies and server-side
  revocation. Authenticated mutations require CSRF tokens and same-origin checks.
- Purpose-specific versioned consent (`consent_records`) governs terms, sensitive dietary
  data, location routing, taste learning, model processing, and analytics, requiring user
  review upon notice revisions and preserving audit trails on withdrawal.
- Room, participant, owner and organizer checks run on the server. Shared results
  omit private check-in contents and attributed dietary information.
- Owner-scoped private meal origins (`meal_origins`) support precise coordinates,
  approximate areas, or opting out, with explicit route processing consent. Private
  origins are never exposed to other participants, shared responses, or model prompts.
- Deterministic route evidence arrival checks apply when firm route evidence exists,
  evaluating meal start, kitchen last orders, and individual `must_leave_by` constraints.
  Missing or stale routing remains unknown and preserves neutral ranking without guessing.
- Structured accessibility requirements (step-free entrance, wheelchair seating, accessible restroom,
  low-noise seating) gate candidate publication using deterministic catalog evidence with freshness
  and review status checks. Missing accessibility evidence remains unknown and never defaults to accessible.
- Session-specific preparation and cross-contact confirmations (`preparation_confirmations`) allow
  bounded staff or kitchen confirmations to verify exact allergen and dietary claims for a meal,
  without letting scraped menus prove absence or storing inferred medical conclusions.
- Profiles and check-ins have explicit unknown states. Declared allergies and
  free-text must-avoid ingredients require additional verification; scraped menus
  do not establish preparation safety. This pilot has no restaurant-confirmation
  integration and never bypasses those unresolved requirements.
- Membership/profile/response changes invalidate active recommendations, including
  upcoming selected meals that need reconfirmation. A decision requires current
  evidence and every included participant's approval.
- Only reviewed main meals/sets with known individual servings, a confirmed dine-in
  channel, complete unit prices and minimum quantity one can pass this pilot. Shared
  dishes, per-piece/weight prices and minimum orders need a future order planner.
- Nearby outlets are enumerated before menu matching. Canonical duplicates do not
  receive extra ranking votes; retrieval score is not treated as factual evidence.
  The explicit-fit baseline and its weights are hypotheses, not a trained model or
  a claim that all recommendation bias has been removed.
- In-app notifications, scoped polling, account export/deletion, expiring invites,
  private post-visit feedback, optional bounded feedback observations, and privacy-safe
  web push subscriptions (`web/sw.js`) work locally.
- Durable in-app check-in and post-meal reminders run with duplicate suppression.
  SMTP verification/recovery is optional and disabled until configured. A restricted
  local operations view shows sanitized stage and usage summaries. See
  [operations configuration](docs/OPERATIONS.md); live mail/push is not verified.
- Recommendation requests return a persisted job immediately. Reloading or closing
  the browser does not cancel processing. Leases, at most three attempts and context/
  evidence checks prevent stale publication. Sanitized attempt history survives
  regeneration; private inputs are not copied into receipts. See [worker details](docs/OPERATIONS.md).
  External LangSmith tracing is disabled by default and supports only privacy-reviewed structural
  metadata export (`dining/tracing.py`) with strict sentinel and PII redaction.
- Known date-specific exceptions override weekly hours. Current evidence must cover
  arrival, last orders and planned finish. Missing kitchen/date information remains
  unverified; the old holiday-known checkbox alone is insufficient.

## Tests and project layout

```bash
python -m pip install -r requirements-test.txt
python -m pytest tests -q
ruff check dining webapp.py tests
ruff format --check dining webapp.py tests
node --check web/app.js
# Optional standalone browser acceptance suite (Node 22+):
npm ci
npx playwright install chromium webkit
npm run test:e2e
```

`web/` owns the responsive interface; `dining/api.py` and `dining/store.py` own
authenticated application state; `dining/catalog.py` is the collector contract;
`dining/routing.py` owns the provider-neutral routing contract;
`dining/geocoding.py` owns the provider-neutral geocoding and neighbourhood lookup contract;
`dining/recommendation.py` owns deterministic eligibility/ranking; `dining/agent.py`
owns the bounded LangGraph flow; `dining/generation.py` owns persisted job attempts.
Browser tests start a disposable fictional-data server and isolated diner contexts.
They never use the running demo or real catalog. The GitHub Actions configuration
is included but has not been run remotely in this task. [Implementation status](docs/WEBAPP_STATUS.md)
records important limits before a real pilot.

## Original course prototype

The original Gradio implementation is retained as `app.py`, with its existing MCP,
Chroma and model configuration. Its California data is not mixed into the new web
app. The new pilot currently uses structured/keyword matching as a transparent
baseline; a catalog-versioned Chroma adapter can be added after the real local
corpus and multilingual retrieval judgments exist.

### Connoisseur Companion

A local restaurant recommendation app built with Gradio, LangGraph, an MCP server, Chroma, and Ollama. Six separately configured agents update a dining profile, retrieve candidates, analyze food trends, styles, and dietary information, then assemble recommendations. The MCP server provides restaurant lookup, reviews, and semantic restaurant, recipe, and food image search.

The included California restaurant, review, recipe, and synthetic image data comes from the course project. Recommendations are based on these static sample records, not live restaurant listings. Dietary information is informational and should be verified with the restaurant.

## Run locally

Prerequisites: Python 3.12 and [Ollama](https://ollama.com/).

```bash
ollama pull llama3.1:8b
python3.12 -m venv .venv312
source .venv312/bin/activate
python -m pip install -r requirements-app.txt
python build_vector_index.py
python app.py
```

Open `http://127.0.0.1:7860/`. Ollama must be running while the app is in use. To change the chat model, set `OLLAMA_MODEL`; to change its OpenAI-compatible endpoint, set `OLLAMA_BASE_URL`. Set `GRADIO_SERVER_PORT` to use another port.

The first index build downloads the MiniLM and CLIP embedding models. The vector index and model cache are generated locally and are excluded from Git. Rebuild the index after changing the data or moving the project to another computer, since indexed image paths point to local files.

## MCP tools

Run `python client.py` to connect to the MCP server over stdio and exercise its data tools. `server.py` exposes restaurant and review lookup, vibe search, semantic restaurant and recipe search, text-to-image and image-to-image search, plus multimodal retrieval. The Gradio app starts the MCP server as a local subprocess when handling a request.

## Project layout

- `app.py`: Gradio interface and local Ollama chat model.
- `workflow.py` and `agent_configs.py`: LangGraph state, agents, and orchestration.
- `server.py` and `vector_search.py`: MCP tools and Chroma retrieval.
- `build_vector_index.py`: MiniLM text and CLIP image embedding pipeline.
- `structured_restaurant_data.json`, `augmented_food_recipe.json`, `augmented_user_review.json`, and `synthetic_recipe_images/`: sample project data.

See [VECTOR_RETRIEVAL.md](VECTOR_RETRIEVAL.md) for the retrieval design and setup details.
