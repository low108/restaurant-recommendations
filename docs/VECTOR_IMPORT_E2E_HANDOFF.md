# Catalog review, vector import, and recommendation E2E handoff

Use this document as the implementation brief for the next coding session. The
input catalog is structurally valid, but it is **not approved for vector indexing
or live recommendations yet**. Preserve that fail-closed behavior.

## Input pinned for this handoff

```text
Repository: /Users/johnathanjohnathan/Documents/restaurant-recommendations
Source:     /Users/johnathanjohnathan/Documents/restaurant-menu-collection/catalog.real.json
SHA-256:    767e25e39957aba4ccb46e49ee136551ebbbc89afe0272282908ce307cf27e77
Catalog:    kl-selangor-real-pilot
Version:    0.1.0-partial
Input schema: 1 (loads successfully and upgrades to schema 2)
```

Do not silently continue if the checksum changes. Re-run the audit and record a
new review snapshot.

## Validation result from 6 October 2026

The built-in catalog loader accepted the file and preserved referential
integrity. It contains:

- 140 sources, 123 brands, 128 outlets, and 2,438 menu items;
- no duplicate source, brand, outlet, or item IDs;
- no dangling source references;
- no exact duplicates under `(outlet, normalized name, normalized variant,
  menu version)`;
- 128 outlets with at least one menu item.

It is not operationally ready:

- 0 reviewed items; 2,437 are unreviewed and 1 is quarantined;
- 0 sources are currently usable for embedding;
- 0 sources are currently usable for display;
- all 140 sources have no expiry date;
- embedding rights are 113 `prohibited` and 27 `unknown`;
- display rights are 113 `prohibited`, 26 `unknown`, and 1 `allowed`, but the
  allowed source is not current because it has no expiry date;
- only 2 of 128 outlets have coordinates;
- only 11 of 128 outlets have published hours;
- all 2,438 menu items have an unknown meal role and unknown serving range;
- 13 items have no structured price;
- the other 2,425 prices have an unknown unit and minimum quantity;
- 2,409 items lack a known payable total;
- 2,329 item descriptions are blank;
- no item has complete dietary or allergen evidence;
- 108 outlet IDs are scan-derived placeholders and need an identity review;
- 69 menu names are very short or placeholder-like and need manual review;
- `ditaliane-ioi-alfredo-funghi-fettuccine-3-pcs-beef-meatballs` is correctly
  quarantined because its vegetarian claim conflicts with the beef variant.

The 20-item pattern across many scan-derived outlets also suggests collection
truncation. Treat this as partial menu coverage, not a complete restaurant menu.

## Non-negotiable activation gates

Do not turn `unknown` or `prohibited` rights into `allowed` merely to make an
index build pass. An operator must review the actual source terms and record the
basis separately for display and embedding. Public access and robots permission
do not establish reuse permission.

An item may enter the production vector collection only when all of these are
true:

1. Every source attached to the outlet and item has `rights.embed = allowed`.
2. Every source has an `expires_at` later than index-build time.
3. The item has `review_status = reviewed` and is not quarantined.
4. Outlet identity has been reviewed and its coordinates are verified.
5. The source text is data, never an instruction for the model or agent.

Eligibility for a recommendation remains stricter than indexing. A published
option must also pass current display rights, dated opening and kitchen evidence,
meal-role and serving checks, complete price checks, dietary and accessibility
requirements, distance/routing policy, and all existing deterministic safety
rules. Vector similarity is candidate recall only.

## First command: reproduce the audit

Run from the repository root:

```bash
set -euo pipefail

SOURCE=/Users/johnathanjohnathan/Documents/restaurant-menu-collection/catalog.real.json
EXPECTED_SHA=767e25e39957aba4ccb46e49ee136551ebbbc89afe0272282908ce307cf27e77
STAGING=var/catalog-import/kl-selangor-real-pilot-0.1.0-partial

test "$(shasum -a 256 "$SOURCE" | awk '{print $1}')" = "$EXPECTED_SHA"
mkdir -p "$STAGING"

.venv/bin/python -m dining.catalog.cli "$SOURCE" \
  --upgrade \
  --install "$STAGING/catalog.v2.json" \
  --report "$STAGING/audit.json"

jq '.summary' "$STAGING/audit.json"
test "$(jq -r '.summary.status' "$STAGING/audit.json")" = review_required
test "$(jq -r '.summary.sources_current_for_embed' "$STAGING/audit.json")" = 0
```

The last two assertions document the current expected result. They are a stop
condition for production indexing, not checks to delete. Store operator-reviewed
corrections as a new catalog version and retain the original file and checksum.

## Implementation work after approval

The repository already has `chromadb`, `sentence-transformers`, `numpy`, and the
in-memory retrieval prototype. Complete the production path instead of building
a second recommendation system.

### 1. Build a persistent, versioned Chroma index

Add `scripts/build_catalog_vector_index.py` with an interface like:

```bash
.venv/bin/python scripts/build_catalog_vector_index.py \
  --catalog var/catalog-import/kl-selangor-real-pilot-0.2.0-reviewed/catalog.v2.json \
  --persist-dir var/vector/catalog \
  --model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
  --rebuild
```

Requirements:

- load through `dining.catalog.models.load_catalog`;
- refuse a catalog with no embedding-eligible records;
- index only reviewed menu items whose item and outlet evidence is currently
  allowed for embedding;
- build the embedded text from allowlisted public fields such as item name,
  reviewed description, cuisine tags, attributes, meal role, and variant;
- never embed allergies, medical data, private free text, exact user locations,
  email addresses, feedback text, credentials, or raw source evidence;
- use stable item IDs and metadata containing `catalog_id`, `catalog_version`,
  `outlet_id`, `item_id`, `embedding_model`, model revision, and
  `index_policy_version`;
- give the collection a deterministic versioned name; build a new collection
  and atomically switch a small manifest instead of mutating the active one;
- delete or retire stale collections after the switch succeeds;
- persist an index manifest with source-catalog SHA-256, counts, versions,
  build timestamp, exclusions by reason, and collection name;
- never print vectors, source text, or private queries to logs.

Pin the embedding model and revision in configuration. The named multilingual
model is an initial candidate because the product needs English, Bahasa Melayu,
and mixed-language recall; it must pass the repository's versioned retrieval
judgment set before activation. Tests must use a deterministic fake embedder and
must not download models.

### 2. Connect retrieval to the live agent

The current implementation is incomplete in three specific ways:

1. `webapp.create_app()` creates `DiningAgent(catalog)` without a vector index.
2. `DiningAgent` creates `Recommender(catalog)` without accepting an index.
3. `Recommender` imports `semantic_candidate_search`, but never calls it; its
   `retrieval_status = semantic` value currently only means a matching in-memory
   index object was supplied.

Fix those seams. The live sequence must be:

```text
private profiles and current check-ins
  -> safe taste-only projection
  -> bounded retrieval query plan
  -> semantic_candidate_search tool
  -> unique outlet IDs with at most two representative items per outlet
  -> deterministic eligibility tool
  -> deterministic group scoring tool
  -> shared diverse shortlist
  -> owner-scoped personal scoring from the same eligible pool
```

The recommender must loop over the bounded retrieved outlet set when semantic
retrieval succeeds. If the index is missing, stale, empty, or unavailable, use
the existing structured whole-catalog path and emit a truthful fallback receipt.
An empty index must not be reported as semantic success.

Preserve outlet-level aggregation: mean of the top two distinct menu-item
similarities. Never sum every menu vector, because large menus would gain an
unfair advantage. Keep recipes in a separate collection if recipe retrieval is
ever added; restaurant recommendations must end with evidence from actual menu
items.

Follow the detailed contracts and personal-result privacy rules in
`docs/SEMANTIC_RETRIEVAL_TICKETS.md`.

### 3. Add the import status surface

Update `/api/catalog/status` so it reports real, non-sensitive index metadata:

```json
{
  "retrieval": {
    "mode": "semantic",
    "index_available": true,
    "catalog_version": "0.2.0-reviewed",
    "embedding_model": "...",
    "index_policy_version": "...",
    "indexed_item_count": 1234,
    "indexed_outlet_count": 92
  }
}
```

Do not expose documents, vectors, source excerpts, user queries, or private
rankings.

## Required tests

Write tests before changing the implementation, then run these groups.

### Catalog and index contract

- unknown, prohibited, expired, unreviewed, and quarantined records are excluded;
- current approved records are indexed once under stable IDs;
- changed catalog/model/policy versions make an old index unusable;
- interrupted builds leave the prior active index intact;
- rebuilding is idempotent and stale vectors are removed;
- no private or firm-requirement sentinel appears in embedded text or metadata;
- duplicate menu rows and 100 extra irrelevant items do not improve an outlet's
  score;
- the per-outlet representative-item cap is enforced;
- an empty approved corpus yields a blocked build, not a successful empty index.

### Agent integration

- a retrieval spy proves `DiningAgent -> Recommender ->
  semantic_candidate_search` is called;
- only retrieved outlet IDs reach eligibility when semantic retrieval succeeds;
- blocked candidates never become eligible even at similarity 1.0;
- stale/unavailable/empty indexes use structured fallback and label it correctly;
- receipt metadata records catalog, embedding, index, and policy versions;
- shared responses never expose full queries or private personal ranking;
- personal alternatives come only from the current group-eligible pool and are
  visible only to their owner.

### End-to-end journeys

Run the fictional reviewed flow first. It must cover two or more diners with
different tastes, semantic retrieval, deterministic filtering and scoring,
shared shortlist, private personal alternatives, veto, unanimous selection, and
post-meal feedback.

Then run a real-catalog smoke test. With the current `0.1.0-partial` file, the
expected result is fail-closed: zero indexed records, `review_required`, no
published options, and no restaurant/source text leak. After an independently
reviewed catalog version exists, repeat the same flow and manually inspect the
receipts and recommendations.

Commands:

```bash
.venv/bin/python -m pytest \
  tests/test_catalog.py \
  tests/test_catalog_audit.py \
  tests/test_semantic_retrieval.py \
  tests/test_recommendation.py \
  tests/test_real_catalog_flow.py \
  tests/test_workflow_completion.py \
  tests/test_full_e2e_journey.py -q

.venv/bin/python -m pytest tests -q

# Browser flow uses an isolated fictional database and disables live inference.
npm run test:e2e
```

Do not claim real recommendation quality from fictional fixtures. Do not call
ILMU for food-safety or eligibility decisions. A separate opt-in live ILMU test
may check explanation labels after the deterministic flow passes.

## Current test evidence

Run on 6 October 2026:

- targeted catalog/retrieval/recommendation/workflow tests: **64 passed**;
- supplied real catalog smoke: **0 indexed items, 0 indexed outlets,
  `needs_verification`, 0 options**, which is the correct fail-closed result;
- full Python suite: **381 passed** after allowing the local fixture server to
  bind;
- standalone browser suite: **36 passed** across desktop Chromium, 320 px
  Chromium, and mobile WebKit, including the actual fictional ranker journey,
  private check-ins, durable shortlist, veto and unanimous choice;
- these green suites use reviewed fictional catalog fixtures. The real corpus
  remains deliberately blocked and cannot yet produce an approved shortlist.

## Definition of done

The handoff is complete only when:

1. a new reviewed catalog version satisfies the rights and evidence gates;
2. the persistent index build reports nonzero approved items and outlets;
3. the live agent actually calls retrieval and eligibility/scoring tools in the
   declared order;
4. an empty or stale index falls back honestly;
5. group and owner-private outputs remain separated;
6. every targeted test, the full Python suite, and the browser suite pass;
7. the real-catalog E2E result contains only evidence-backed restaurants and
   items; and
8. the review report records limitations instead of claiming that retrieval
   quality or food suitability has been proven.
