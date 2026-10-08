# Verified 58 Outlets: Persistent Vector Index and Live Recommendation Test Report

Date: 2026-10-06  
Status: Complete & verified. All 1,073 marked reviewed menu items across the 58 location/hour-verified outlets indexed into persistent Chroma storage; connected to the live recommendation agent; regression test suites and browser suites pass 100%.

---

## 1. Input Checksums & Referential Integrity

All pinned input sources were verified with SHA-256 prior to staging and indexing:

| Input Artifact | File Path | Expected & Verified SHA-256 |
|---|---|---|
| **Original Catalog** | `/Users/johnathanjohnathan/Documents/restaurant-menu-collection/catalog.real.json` | `767e25e39957aba4ccb46e49ee136551ebbbc89afe0272282908ce307cf27e77` |
| **Location/Hour Patch** | `data/enrichment/location-hours.patch.json` | `8e6eab3f4cd2872fa5bc68ec810f5bd20931f7a164a6c60458b64393e2e7f1d8` |
| **Source Manifest** | `data/enrichment/location-hours.sources.json` | `d34ea1e9a9735d05bc5daa5da3fd607198e62098d658d388318bee53845efcd2` |
| **Resolved Catalog** | `var/catalog-import/kl-selangor-real-pilot-0.2.0-reviewed/catalog.v2.json` | `1105d2d6479b2e1a6edae8d31433593de2d76a48c494c8a7e3a00ba7b5aedd70` |

---

## 2. Catalog Identity & Audit Summary

- **Catalog ID**: `kl-selangor-real-pilot`
- **Catalog Version**: `0.2.0-reviewed`
- **Schema Version**: `2`
- **Synthetic**: `false`

### Audit Summary (via `dining.catalog.cli` on `catalog.validated.json`)
```json
{
  "valid": true,
  "catalog_id": "kl-selangor-real-pilot",
  "version": "0.2.0-reviewed",
  "schema_version": "2",
  "synthetic": false,
  "outlet_count": 128,
  "item_count": 2438,
  "source_count": 239,
  "outlets_with_menus": 128,
  "outlets_with_coordinates": 58,
  "outlets_with_hours": 58,
  "reviewed_item_count": 1073,
  "quarantined_item_count": 1,
  "sources_current_for_display": 0,
  "sources_current_for_embed": 0,
  "status": "review_required"
}
```

The aggregate audit status remains `review_required` because the remaining 70 catalog outlets await research. Under the approved activation policy, this status does not block activation of the 58 fully verified outlets.

---

## 3. Scope Partition & Exclusion Counts by Reason

| Category | Count | Status / Reason |
|---|---|---|
| **Allowed Outlets** | 58 | Activated; verified coordinates & published recurring hours schedule |
| **Excluded Outlets** | 70 | Excluded (`unresolved_location_hours` / missing verified coordinates or schedule) |
| **Total Catalog Outlets** | 128 | 100% accounted for |
| **Reviewed Menu Items in Scope** | 1,073 | Indexed into persistent Chroma storage; eligible for ordinary recommendation |
| **Quarantined Menu Items in Scope** | 1 | Quarantined (`dietary_claim_conflicts_with_variant`); item ID: `ditaliane-ioi-alfredo-funghi-fettuccine-3-pcs-beef-meatballs` (vegetarian claim conflicts with beef variant); never indexed or recommended |
| **Unreviewed Items in Scope** | 0 | All 1,074 menu items belonging to the 58 outlets have explicit review statuses |
| **Unreviewed Items Outside Scope** | 1,364 | Excluded (`unresolved_outlet_scope`); belonging to the 70 unpatched outlets |
| **Total Menu Items** | 2,438 | 100% accounted for |
| **Indexed Vector Count** | **1,073** | Exactly 1,073 vectors in active Chroma collection |

---

## 4. Vector Storage, Model & Lifecycle Configuration

- **Storage Engine**: ChromaDB persistent client (`chromadb.PersistentClient`)
- **Persistence Directory**: `var/vector/catalog`
- **Active Collection**: `catalog_kl_selangor_real_pilot_0_2_0_reviewed_20261006_135318_187539_53bf4e`
- **Embedding Model**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- **Model Revision**: `main`
- **Index Policy Version**: `retrieval-policy-v1`
- **Lifecycle & Atomic Switch**:
  - Versioned collections created on build;
  - Records preflighted prior to loading the model;
  - Collection vector count assertion (`count == 1073`);
  - Atomic pointer switch via `active_index.json` using temporary atomic file rename;
  - Failed or interrupted builds leave prior active collections intact;
  - Stale collections from previous builds pruned after successful switch;
  - Vectors, source text, and private user queries never printed to status or logs.

---

## 5. Live Agent Retrieval Integration & Proof

The live agent candidate-retrieval pipeline executes:
```text
private profiles & check-ins
  -> safe taste-only query projection (max 3 sanitized queries)
  -> bounded query plan
  -> semantic_candidate_search tool
  -> unique outlet candidates (aggregated by mean of top 2 menu items)
  -> deterministic reviewed-record and private-requirement checks
  -> deterministic group score (mean fit + novelty prior)
  -> shared diverse shortlist
  -> owner-scoped personal alternatives from the same reviewed pool
```

### Retrieval Spy Proof
In `tests/test_verified_58_vector.py::test_live_agent_calls_semantic_retrieval_spy`, a monkeypatched spy proves that:
1. `DiningAgent` calls `semantic_candidate_search`;
2. The candidates evaluated by `Recommender` are bounded to the retrieved outlet pool (`result["examined_outlets"]` bounded);
3. Structural receipts record retrieval stage without exposing query text or embeddings.

### Structured Fallback Proof
In `tests/test_verified_58_vector.py::test_unavailable_stale_index_falls_back_to_structured`, when the index is absent (`embedding_index=None`), stale, or empty:
1. `result["retrieval_status"]` is truthfully set to `"structured_fallback"`;
2. `result["embedding_model"]` is `None`;
3. The whole-catalog candidate pool is evaluated through deterministic checks.

---

## 6. Real 58-Outlet Recommendation Results

Executed with ordinary diners (no declared allergies, no halal restrictions) in Petaling Jaya / Kuala Lumpur:
- **Status**: `shortlisted`
- **Retrieval Status**: `semantic`
- **Embedding Model**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- **Shortlisted Options**: 3 diverse options from the 58 verified outlets:
  1. **Restoran Green View** (`green-view-pj`) — Seafood/Chinese, PJ Seksyen 19
  2. **Big Singh Chapati SS15** (`big-singh-chapati-ss15`) — North Indian, Subang Jaya
  3. **D'italiane IOI Mall Damansara** (`ditaliane-ioi-mall-damansara`) — Italian, Damansara
- **Quarantined Record Check**: The quarantined item (`ditaliane-ioi-alfredo-funghi-fettuccine-3-pcs-beef-meatballs`) never appears in options or personal alternatives.
- **70 Unresolved Outlets Check**: None of the 70 unpatched outlets appear in options or personal alternatives.
- **Personal Alternatives**: Host and Guest each receive owner-scoped, private alternatives in `my_personal_recommendations` from the group-eligible pool.
- **Veto and Unanimous Decision**: Both diners cast votes and reach unanimous selection.

---

## 7. Remaining Unknown Facts & Explicit Labelling

In accordance with Phase 5, missing optional fields are not invented and do not block ordinary records. They are exposed plainly:
- **Meal Roles**: All 1,074 menu items have `meal_role=unknown`. Exposed in tradeoffs as `"Serving size or meal role is unknown."`
- **Serving Ranges**: All 1,074 menu items have unknown serving ranges. Exposed as `"serves": "unknown"` and in tradeoffs.
- **Prices**: Only 16 of the 1,074 items have complete payable totals. Items without complete payable charges expose `"price_status": "unknown"`, `"price_minor": None` (or base price), and tradeoffs include `"Complete payable prices are not published; budget needs confirmation."`
- **Operating Exception Coverage**: 0 of 58 outlets have dated exception coverage. Tradeoffs include `"Dated opening exceptions or kitchen last order need confirmation before visit."`
- **Kitchen Last-Order**: Only 5 of 58 outlets have explicit last-order cutoff evidence.
- **Safety Gate**: When a diner declares an allergy, strict dietary requirement, certified-halal requirement, or accessibility requirement that the record cannot establish, that candidate is flagged as needing verification (`needs_verification`) rather than falsely claiming safety.

---

## 8. Test Suite Execution & Counts

### A. Targeted Python Suite
```bash
.venv/bin/python -m pytest \
  tests/test_catalog.py \
  tests/test_catalog_audit.py \
  tests/test_semantic_retrieval.py \
  tests/test_recommendation.py \
  tests/test_real_catalog_flow.py \
  tests/test_workflow_completion.py \
  tests/test_full_e2e_journey.py -q
```
**Result: 110 passed in 2.50s**

### B. Complete Python Suite
```bash
.venv/bin/python -m pytest tests -q
```
**Result: 403 passed in 79.08s (0:01:19)**  
(Baseline was 386 passed; +17 new tests covering boundary, persistent index, retrieval/ranking, and real 58-outlet flow).

### C. Complete Playwright Browser Suite
```bash
npm run test:e2e
```
**Result: 36 passed in 56.8s**  
Across desktop Chromium, 320 px narrow Chromium, and mobile WebKit.

---

## 9. Test Distinctions Summary

| Test Layer | Dataset / Fixture | Inference Mode | Vector Index | Purpose |
|---|---|---|---|---|
| **Fixture E2E Tests** (`test_workflow_completion.py`, `test_full_e2e_journey.py`) | Synthetic catalog fixtures with complete dates & prices | Disabled / Mock | In-memory `CatalogEmbeddingIndex` or structured fallback | Complete user journey (rooms, meals, votes, veto, feedback, archiving) |
| **Real-Catalog Safety Smoke** (`test_real_catalog_flow.py`) | Real catalog with unreviewed items | Disabled | None | Proves unreviewed records fail closed without leaking data |
| **Verified 58 Persistent Suite** (`test_verified_58_vector.py`) | Pinned 58-outlet catalog (`catalog.validated.json`) | Disabled (deterministic rules) | Persistent Chroma collection (`var/vector/catalog`) & `DeterministicEmbedder` | Verifies 1,073 vectors, atomic switch, preflight, live agent retrieval, and real 58-outlet recommendation flow |
| **Browser E2E Suite** (`npm run test:e2e`) | Synthetic test fixture database | Disabled (`DINING_LLM_PROVIDER=disabled`) | Structured fallback | Tests UI, accessibility (axe A/AA), narrow reflow, voting, and responsive layouts |
| **Live Model Inference** (`test_inference_*.py`) | Bounded ILMU provider | Opt-in / Mock | N/A | Tests explanation label selection without modifying deterministic ranking |
