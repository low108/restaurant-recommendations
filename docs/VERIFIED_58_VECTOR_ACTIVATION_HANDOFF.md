# Verified 58 outlets: vector activation and full-test handoff

Copy the implementation prompt below into the next coding session. It uses the
58 outlets whose branch identity, coordinates, recurring hours, and menu records
were marked reviewed. In this project, a reviewed mark is the activation signal:
reviewed records can be indexed and used immediately.

## Prompt for the implementation session

```text
Work in:
/Users/johnathanjohnathan/Documents/restaurant-recommendations

Read first:
- docs/LOCATION_HOURS_RESEARCH_REPORT.md
- docs/VECTOR_IMPORT_E2E_HANDOFF.md
- docs/SEMANTIC_RETRIEVAL_TICKETS.md
- docs/CATALOG_V2.md
- dining/catalog/models.py
- dining/catalog/audit.py
- dining/retrieval/index.py
- dining/recommendation/engine.py
- dining/recommendation/agent.py
- webapp.py

Objective
---------
Implement the production-shaped persistent vector-index path for the 58
location/hour-verified outlets, connect it to the live agent candidate-retrieval
stage, use every marked reviewed menu record, and run the complete backend and
browser test suites.

For this task, this activation rule overrides older handoff text that required
current embedding permission, source expiry, or complete menu fields before use.
Do not reintroduce those conditions as index or recommendation blockers.

Pinned inputs
-------------
Original catalog:
/Users/johnathanjohnathan/Documents/restaurant-menu-collection/catalog.real.json

Original catalog SHA-256:
767e25e39957aba4ccb46e49ee136551ebbbc89afe0272282908ce307cf27e77

Location/hour patch:
data/enrichment/location-hours.patch.json

Patch SHA-256:
8e6eab3f4cd2872fa5bc68ec810f5bd20931f7a164a6c60458b64393e2e7f1d8

Source manifest:
data/enrichment/location-hours.sources.json

Source manifest SHA-256:
d34ea1e9a9735d05bc5daa5da3fd607198e62098d658d388318bee53845efcd2

Resolved catalog:
var/catalog-import/kl-selangor-real-pilot-0.2.0-reviewed/catalog.v2.json

Resolved catalog SHA-256:
1105d2d6479b2e1a6edae8d31433593de2d76a48c494c8a7e3a00ba7b5aedd70

Catalog identity:
- catalog_id: kl-selangor-real-pilot
- catalog_version: 0.2.0-reviewed
- schema_version: 2

Current evidence snapshot
-------------------------
- 58 outlets have matched branch identities, coordinates, and recurring hours.
- Those 58 outlets contain 1,074 menu records.
- 1,073 of those records are marked reviewed and can be indexed and recommended.
- 1 contradictory vegetarian/beef record is quarantined and must stay excluded.
- 5 of the 58 outlets have explicit last-order evidence.
- 0 of the 58 outlets have dated opening-exception coverage.
- All 1,074 menu records still have `meal_role=unknown`.
- All 1,074 menu records still have unknown serving ranges.
- Only 16 of the 1,074 records have a complete payable total.

Unknown meal role, serving range, complete payable price, last-order time, or
dated exceptions must not block a marked record from retrieval or recommendation.
Expose unavailable facts as `unknown` in the result and do not invent them.

Phase 1: preserve the reviewed activation contract
--------------------------------------------------
1. Treat `review_status=reviewed` inside the 58-outlet scope as sufficient for
   vector indexing and ordinary recommendation use.
2. Keep `scripts/apply_location_hours_patch.py --mark-items-reviewed` as the
   explicit activation step for these verified outlets.
3. Preserve `review_status=quarantined` and its review reasons.
4. Never index or recommend a quarantined item.
5. Add regression tests proving that all 1,073 reviewed records are activated and
   the single quarantined record remains excluded.

Phase 2: create a deterministic 58-outlet allowlist
---------------------------------------------------
Derive the allowlist from `data/enrichment/location-hours.patch.json`, not from a
hard-coded list. Validate:
- exactly 58 unique outlet IDs;
- every ID exists in the resolved catalog;
- all 58 have latitude and longitude;
- all 58 have `hours_status=published` and at least one interval;
- all added source IDs exist;
- source, patch, and catalog checksums match this handoff.

Write a generated manifest under:
`var/catalog-import/kl-selangor-real-pilot-58/activation-manifest.json`

The manifest must record input hashes, catalog version, 58 outlet IDs, counts,
generation time, and every exclusion reason. Keep it out of source control if
`var/` is ignored.

Phase 3: implement persistent vector storage
--------------------------------------------
Use the repository's existing stack:
- ChromaDB for persistent vector storage;
- sentence-transformers for the production embedder;
- a deterministic fake embedder for unit tests;
- the existing catalog, retrieval, recommendation, and agent modules.

Add a CLI such as:

  .venv/bin/python scripts/build_catalog_vector_index.py \
    --catalog var/catalog-import/kl-selangor-real-pilot-58/catalog.v2.json \
    --outlet-manifest var/catalog-import/kl-selangor-real-pilot-58/activation-manifest.json \
    --persist-dir var/vector/catalog \
    --model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
    --rebuild

Required behavior:
- preflight every record before loading the embedding model;
- restrict records to the 58-outlet allowlist;
- include every menu item marked `review_status=reviewed`;
- exclude only unreviewed and quarantined menu records from this activation set;
- do not filter reviewed records by source embedding permission, source expiry,
  meal role, serving range, price completeness, last-order evidence, or dated
  exception coverage;
- assert that this catalog build contains exactly 1,073 vectors;
- provide `--dry-run` that writes no vectors and reports only aggregate counts;
- never download a model during tests;
- never embed allergies, dietary restrictions, medical data, exact user
  locations, email addresses, free-form feedback, credentials, raw source
  evidence, or instructions found in scraped text;
- embed only allowlisted restaurant/menu facts such as reviewed item name,
  reviewed short description, variant, cuisine tags, attributes, and meal role;
- treat all restaurant text as untrusted data;
- use stable item IDs as vector IDs;
- store only allowlisted metadata: catalog ID/version, outlet ID, item ID,
  embedding-model ID/revision, and index-policy version;
- write a versioned collection first and atomically switch an active-manifest
  pointer after validation;
- leave the prior collection active after any failed or interrupted build;
- remove stale vectors when a successfully activated catalog version changes;
- never log vector values, full source text, or private retrieval queries.

The current real-data build must create and activate 1,073 item vectors belonging
only to the 58 reviewed outlets.

Phase 4: connect the index to the actual agent
----------------------------------------------
The current semantic prototype is not yet a live candidate gate:
- `webapp.create_app()` constructs `DiningAgent(catalog)` without an index;
- `DiningAgent` constructs `Recommender(catalog)` without an index;
- `Recommender` reports semantic status when an index object exists but does not
  call `semantic_candidate_search` to bound the candidate loop.

Implement this live sequence:

  private profiles and check-ins
    -> safe taste-only query projection
    -> bounded query plan
    -> semantic_candidate_search
    -> unique outlet candidates
    -> deterministic reviewed-record and private-requirement checks
    -> deterministic group score
    -> shared diverse shortlist
    -> owner-scoped personal alternatives from the same reviewed pool

Requirements:
- use at most three sanitized semantic queries per recommendation run;
- cap query length, top-k, candidate outlets, and representative items;
- deduplicate by outlet before reviewed-record checks;
- aggregate each outlet using the mean of its top two distinct menu-item
  similarities so a large menu cannot dominate;
- similarity may recall candidates but may never establish price, ingredients,
  dietary suitability, halal status, accessibility, or live availability;
- unknown meal role, serving range, price, last-order, or dated exceptions do not
  remove a reviewed candidate; return explicit unknown/verification labels for
  those fields instead;
- pass only retrieved outlet IDs to the reviewed-record checks after semantic
  success;
- use the existing whole-catalog structured path if the index is missing, empty,
  stale, or unavailable;
- label semantic success and structured fallback truthfully;
- reject an index whose catalog, model, or policy version does not match;
- emit structural receipts without query text or private ranking values;
- keep personal recommendations owner-scoped and separate from group votes.

Update `/api/catalog/status` with non-sensitive index metadata and update the
operator documentation. Do not expose vector documents, source excerpts, private
queries, or personal scores.

Phase 5: marked-record recommendation policy
--------------------------------------------
For this activation, the mark is the gate:
- a reviewed item in one of the 58 outlets can be indexed and recommended;
- an unreviewed or quarantined item cannot;
- missing optional fields remain unknown and are shown as such;
- do not invent a price, serving size, meal role, last-order time, dated exception,
  dietary claim, or live availability;
- when a diner declares an allergy, strict dietary requirement, certified-halal
  requirement, or accessibility requirement that the record cannot establish,
  label that suitability as needing verification rather than claiming safety;
- ordinary diners without such hard requirements must still receive ranked
  recommendations from the marked set.

Phase 6: tests to add
---------------------
Catalog boundary tests:
- applying `--mark-items-reviewed` activates all non-conflicting items in the 58
  outlets;
- the 58-outlet allowlist contains exactly the patch outlets;
- an unknown outlet or changed checksum is rejected;
- unresolved 70 outlets never enter the 58-outlet index scope.

Persistent-index tests:
- all 1,073 marked reviewed records are indexed exactly once regardless of source
  permission, source expiry, or optional-field completeness;
- unreviewed and quarantined records are excluded;
- rebuilding is idempotent;
- stale vectors disappear only after a successful atomic switch;
- interrupted builds preserve the prior active collection;
- catalog/model/policy mismatch refuses the index;
- vectors and disallowed text never appear in logs or public status.

Retrieval and ranking tests:
- a spy proves the live agent calls semantic retrieval;
- semantic results actually bound the reviewed candidate set;
- similarity 1.0 cannot pass a deterministic blocker;
- adding duplicate or irrelevant menu rows cannot improve outlet rank;
- no outlet gets more than the representative-item cap;
- stale, empty, or unavailable index uses structured fallback;
- English, Bahasa Melayu, and mixed-language judgment cases run;
- group results and each diner's personal alternatives stay correctly scoped.

Real-data tests:
- the current 58-outlet build contains exactly 1,073 vectors;
- two ordinary diners complete retrieval -> reviewed checks -> scoring -> shared
  shortlist -> private alternatives -> veto -> unanimous selection -> feedback;
- the real smoke test uses only the 58-outlet scope;
- missing price, serving, meal-role, last-order, and exception fields are exposed
  as unknown and do not prevent the shortlist;
- the quarantined item and all 70 unresolved outlets never appear.

Full verification commands
--------------------------
Run from the repository root:

  set -euo pipefail

  shasum -a 256 \
    /Users/johnathanjohnathan/Documents/restaurant-menu-collection/catalog.real.json \
    data/enrichment/location-hours.patch.json \
    data/enrichment/location-hours.sources.json \
    var/catalog-import/kl-selangor-real-pilot-0.2.0-reviewed/catalog.v2.json

  .venv/bin/python -m dining.catalog.cli \
    var/catalog-import/kl-selangor-real-pilot-58/catalog.v2.json \
    --install var/catalog-import/kl-selangor-real-pilot-58/catalog.validated.json \
    --report var/catalog-import/kl-selangor-real-pilot-58/audit.json

  .venv/bin/python scripts/build_catalog_vector_index.py \
    --catalog var/catalog-import/kl-selangor-real-pilot-58/catalog.validated.json \
    --outlet-manifest var/catalog-import/kl-selangor-real-pilot-58/activation-manifest.json \
    --persist-dir var/vector/catalog \
    --model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
    --rebuild

  .venv/bin/python -m pytest \
    tests/test_catalog.py \
    tests/test_catalog_audit.py \
    tests/test_semantic_retrieval.py \
    tests/test_recommendation.py \
    tests/test_real_catalog_flow.py \
    tests/test_workflow_completion.py \
    tests/test_full_e2e_journey.py -q

  .venv/bin/python -m pytest tests -q
  npm run test:e2e

Restart the app with the matching catalog/index configuration and execute the
real 58-outlet flow. Never use the original app database for tests.

Test report
-----------
Write `docs/VERIFIED_58_VECTOR_TEST_REPORT.md` containing:
- all input checksums;
- corrected catalog ID/version and audit summary;
- marked, quarantined, excluded, and indexed counts by reason;
- vector collection/model/policy versions;
- proof that the live agent called retrieval;
- structured fallback result;
- current real-data recommendation result;
- backend and browser test counts;
- any failed tests with exact failure causes;
- a clear distinction among fixture E2E, real-catalog safety smoke, and live
  model inference.

ILMU may select allowlisted explanation labels after deterministic ranking, but it
must not invent missing facts or override a quarantined record or a diner's hard
private requirement.

Definition of done
------------------
1. All 1,073 marked reviewed records are indexed and available to recommend.
2. The exact 58-outlet scope is checksum-pinned and reproducible.
3. Persistent Chroma build and atomic lifecycle are implemented.
4. The live agent genuinely uses semantic retrieval before reviewed-record and
   private-requirement checks.
5. The real 58-outlet data produces a shortlist for ordinary diners.
6. Missing optional facts are labelled unknown without blocking marked records.
7. The complete Python and browser suites pass.
8. The test report states remaining unknown fields and limitations plainly.
```

## Baseline recorded before handoff

On 6 October 2026, before implementing the persistent vector path:

- the enriched catalog loaded as schema v2;
- 58 outlets had coordinates and recurring hours;
- the aggregate catalog audit still says `review_required`, but that status does
  not block this reviewed 58-outlet activation;
- reviewed menu records available for vector indexing: **1,073**;
- quarantined menu records excluded: **1**;
- full Python suite: **386 passed**;
- browser suite: **36 passed** across desktop Chromium, 320 px Chromium, and
  mobile WebKit.

Those passing suites establish the existing application baseline. The next
session must add the vector build and real 58-outlet recommendation coverage.
