# Coordinates and opening-hours research handoff

Copy the prompt below into a separate research session. This task enriches the
restaurant catalog with branch coordinates and operating-hour evidence. It does
not approve menu content, build embeddings, or activate recommendations.

## Prompt for the research session

```text
Work in this repository:
/Users/johnathanjohnathan/Documents/restaurant-recommendations

Input catalog:
/Users/johnathanjohnathan/Documents/restaurant-menu-collection/catalog.real.json

Read these files before doing any research:
- docs/SCRAPING_HANDOFF.md
- docs/CATALOG_V2.md
- docs/VECTOR_IMPORT_E2E_HANDOFF.md
- dining/catalog/models.py
- dining/catalog/audit.py

Goal
----
Use Google Search to find reliable public evidence for the exact coordinates and
recurring opening hours of each outlet in catalog.real.json. Also collect kitchen
last-order times and dated holiday/special-hours evidence when an authoritative
source publishes them.

Google Search is a discovery tool. Do not scrape Google Search result pages or
Google Maps. Do not treat a search-result snippet as evidence. Open the source
page and cite its canonical URL. Prefer the restaurant's official branch page,
official restaurant website, official mall directory, or an official
certification/business directory. A Google Business Profile may be used only
through an authorized interface/API and under its applicable storage and display
terms. If the right to retain or reuse a Google-derived field is unclear, record
rights as unknown and leave the production field inactive.

Do not bypass logins, CAPTCHAs, paywalls, robots restrictions, rate limits, or
other access controls. Do not call or message restaurants. Do not invent facts,
infer opening times from review activity, or copy hours from delivery availability.

Input identity
--------------
Before researching an outlet, match all of the following:
- outlet_id;
- restaurant/brand name;
- branch or mall name;
- street address, city, and state.

Do not merge different branches just because they share a brand. If search results
are ambiguous or the branch appears closed/renamed, mark the record for review.
Never copy coordinates or hours from another branch.

Source priority
---------------
Use the first adequate source in this order:
1. Official restaurant branch/location page.
2. Official mall or venue tenant page for that exact branch.
3. Official restaurant announcement or current menu/contact page.
4. Authorized Google Places/Business Profile data when permitted.
5. A reputable directory only as a secondary cross-check.

For ambiguous identities, require two independent matching sources. Never turn
`rights.display` or `rights.embed` to `allowed` without a documented operator
decision or explicit permission basis. Public availability alone is not an
approval.

Coordinates
-----------
Collect decimal WGS84 latitude and longitude for the exact storefront/branch.

Validation rules:
- latitude must be between -90 and 90;
- longitude must be between -180 and 180;
- the point must be geographically plausible for Kuala Lumpur or Selangor;
- compare it with the catalog address and locality;
- reject a city centroid, postcode centroid, road midpoint, mall-wide centroid,
  or another branch's point unless the product explicitly accepts that precision;
- record coordinate precision as `storefront`, `building`, `mall`, `approximate`,
  or `unknown` in the research report;
- only `storefront` and `building` precision should be proposed for activation;
- if only approximate coordinates are available, leave catalog latitude and
  longitude unchanged and add a review issue.

The current schema associates outlet facts with `outlet.source_ids`. Preserve the
source relationship and explain which source supports the coordinate. Do not
remove an old source merely to make a rights check pass.

Recurring opening hours
-----------------------
Store hours in Asia/Kuala_Lumpur using 24-hour `HH:MM` values. Python weekday
numbers are Monday=0 through Sunday=6.

Handle these cases explicitly:
- split service, such as 11:30-14:30 and 17:30-22:00;
- overnight service with `closes_next_day=true`;
- 24-hour service as 00:00-00:00 with `closes_next_day=true` only when the source
  explicitly says 24 hours;
- closed days by omitting that weekday only when a complete weekly schedule
  explicitly confirms the closure;
- different public opening and kitchen last-order times;
- temporary, Ramadan, holiday, renovation, or event schedules as dated
  exceptions, never as permanent weekly hours.

Only set `hours_status="published"` when a current source establishes the full
weekly schedule, including which days are closed. If the source covers only some
days or says "hours may vary", preserve `hours_status="unknown"` and create a
review issue instead.

Last order is a separate fact. Populate `last_order` and
`last_order_source_ids` only when the source explicitly publishes a kitchen or
last-order cutoff. Do not estimate last order from closing time. Without explicit
last-order evidence, use:

  "last_order": null,
  "last_order_next_day": false,
  "last_order_source_ids": []

This means the current recommendation engine will request verification. That is
correct and must not be weakened.

Dated exceptions
----------------
Recurring hours do not prove that a restaurant will be open on a future public
holiday. Populate `opening_exceptions` only for specifically dated, sourced
exceptions. Use `status="closed"`, `status="published"`, or `status="unknown"`.

Populate `opening_exceptions_coverage` only when a source or explicit review
process establishes that exceptions were checked for the entire stated date
range. Do not claim a range merely because no exception was found in one search.
If full coverage cannot be established, leave it null and report the gap.

Freshness
---------
Every new source needs:
- a stable source_id;
- canonical URL;
- source kind;
- timezone-aware observed_at;
- publisher_updated_at when stated;
- a conservative expires_at based on the project's operator policy;
- short evidence_text describing exactly what the page supports;
- separate display and embedding rights plus a factual basis.

Do not invent an expiry or permission. If no approved freshness policy exists,
leave expires_at null and mark the field as requiring operator review. Do not
copy long copyrighted passages into evidence_text.

Deliverables
------------
Do not overwrite catalog.real.json. Create these files:

1. `data/enrichment/location-hours.patch.json`
2. `data/enrichment/location-hours.review.json`
3. `data/enrichment/location-hours.sources.json`
4. `docs/LOCATION_HOURS_RESEARCH_REPORT.md`

The patch must use this shape:

{
  "patch_version": "1",
  "input": {
    "catalog_id": "kl-selangor-real-pilot",
    "catalog_version": "0.1.0-partial",
    "sha256": "767e25e39957aba4ccb46e49ee136551ebbbc89afe0272282908ce307cf27e77"
  },
  "generated_at": "2026-10-06T00:00:00Z",
  "outlet_updates": [
    {
      "outlet_id": "exact-existing-outlet-id",
      "identity_status": "matched",
      "latitude": 3.000000,
      "longitude": 101.000000,
      "coordinate_precision": "storefront",
      "hours_status": "published",
      "opening_hours": [
        {
          "weekday": 0,
          "opens": "11:30",
          "closes": "22:00",
          "closes_next_day": false,
          "last_order": "21:30",
          "last_order_next_day": false,
          "last_order_source_ids": ["source-id-hours"]
        }
      ],
      "opening_exceptions": [],
      "opening_exceptions_coverage": null,
      "source_ids_to_add": ["source-id-location", "source-id-hours"],
      "field_evidence": {
        "coordinates": ["source-id-location"],
        "opening_hours": ["source-id-hours"],
        "last_order": ["source-id-hours"]
      },
      "review_notes": []
    }
  ]
}

The example values above are placeholders and must never be copied as facts.

For unresolved outlets, add a review entry instead of a factual patch:

{
  "outlet_id": "exact-existing-outlet-id",
  "status": "needs_review",
  "reason_codes": ["ambiguous_branch", "hours_incomplete"],
  "candidate_urls": ["https://example.invalid/discovery-only"],
  "notes": "Brief factual explanation without copied source text"
}

Allowed reason codes are:
- `ambiguous_branch`
- `address_mismatch`
- `possibly_closed`
- `possibly_renamed`
- `coordinates_missing`
- `coordinates_approximate`
- `hours_missing`
- `hours_incomplete`
- `hours_conflict`
- `last_order_missing`
- `holiday_coverage_missing`
- `source_stale`
- `rights_unknown`
- `access_blocked`

Research in checkpoints of no more than 20 outlets. After each checkpoint, save
the outputs and validate that every outlet_id exists in the input catalog, every
new source_id is unique, and every referenced source exists. Keep a progress table
in the Markdown report.

Quality controls
----------------
- Compare proposed coordinates with the catalog address/locality.
- Flag identical coordinates across unrelated outlets.
- Flag coordinates shared by multiple branches unless they are in the same mall.
- Flag conflicting schedules from two current sources.
- Flag a schedule copied identically across every branch of a chain unless an
  official source explicitly confirms chain-wide hours.
- Flag impossible or overlapping intervals.
- Flag same-day closing times not later than opening times.
- Flag last-order times outside the service interval.
- Flag sources with missing timestamps, missing rights basis, or broken URLs.
- Keep missing evidence as unknown; never use false, zero, empty string, or a
  guessed value to make validation pass.

Before completion, produce counts for:
- exact branch identities matched;
- storefront/building coordinates proposed;
- approximate coordinates rejected;
- full weekly schedules proposed;
- explicit last-order schedules proposed;
- dated exception ranges established;
- unresolved outlets by reason;
- sources by kind, freshness state, display right, and embedding right.

Do not build a vector index, edit recommendation code, run live recommendations,
or activate these patches. Another session will review and apply approved records.
```

## Reviewer procedure after the research session

The reviewer should not apply the patch by hand. First verify the source checksum
and inspect every rights/freshness decision. Then implement or use a deterministic
patch applicator that:

1. refuses a mismatched catalog ID, version, or checksum;
2. refuses unknown outlet IDs and duplicate source IDs;
3. validates all new sources and outlet records through `dining.catalog.models.Catalog`;
4. writes a new catalog version atomically;
5. runs `dining.catalog.cli` and saves its audit report; and
6. leaves the previous catalog and vector collection unchanged until review
   succeeds.

Expected validation command after an approved patch has been applied:

```bash
.venv/bin/python -m dining.catalog.cli \
  var/catalog-import/kl-selangor-real-pilot-enriched/catalog.v2.json \
  --install var/catalog-import/kl-selangor-real-pilot-enriched/catalog.validated.json \
  --report var/catalog-import/kl-selangor-real-pilot-enriched/audit.json

jq '.summary | {
  status,
  outlets_with_coordinates,
  outlets_with_hours,
  sources_current_for_display,
  sources_current_for_embed,
  issues
}' var/catalog-import/kl-selangor-real-pilot-enriched/audit.json
```

More coordinates and recurring hours alone will not make the catalog eligible for
recommendations. Menu review, complete prices, serving roles, current display
rights, last-order evidence, and dated exception coverage remain separate gates.
