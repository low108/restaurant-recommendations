# Real restaurant catalog integration

The local app can load a fixed copy of the independently collected catalog, report
its coverage, and hold incomplete or conflicting records out of recommendations.
The collector's working file is never edited or watched for automatic reloads.

## Captured delivery

Captured 5 October 2026 from
`/Users/johnathanjohnathan/Documents/restaurant-menu-collection/catalog.real.json`.
The collector had updated the file since the earlier 11-outlet review. This copy
contains **12 outlets, 549 menu variants, 7 sources, and menus for 3 outlets**.

Input SHA-256:
`dfc250f8206bee503b3b429a99556ece1a97feefe7cdbc2460ca9463724eb11a`

Local, gitignored artifacts:

- `var/catalog.received.dfc250f8206b.json`: original received bytes.
- `var/catalog.real.v2.json`: validated normalized import for this app.
- `var/catalog.import-review.json`: counts, affected IDs, transformations and hashes.

The normalized version is `0.1.0-partial-review-v2-dfc250f8`. These counts describe
this snapshot; the collector may continue changing its own file.

## Changes to this delivery

- Added schema-v2 defaults without inventing missing facts.
- Quarantined eight contradictory dietary records; retained original claims for
  operator review. Six have beef/chicken variants, and two more have contradictory
  chicken wording in their descriptions. These are automated review leads.
- Changed 481 D'italiane/Green View prices to `channel: unknown`, because the supplied
  collection report explicitly identified their dine-in channel as an assumption.
  New records absent from that report retain their supplied, unreviewed assertions.
- Transcribed seven explicit unit strings and two explicit minimum-order notes
  into structured price fields. A printed RM13 per piece with minimum two stays
  RM13 **per unit**, minimum quantity two. It is not a RM13 standalone meal.
- Kept source observation times, rights, expiry, ingredient and allergy unknowns
  as supplied. A build timestamp is not proof that the source was fetched then.

No item has been approved for recommendations. Eight are quarantined and 541
remain unreviewed. All seven sources lack current approved display/embedding
status. Only one location has coordinates, two have published weekly hours.
These blockers are visible as aggregate coverage; imported restaurant prose is
not published through blocked recommendations.

## New fields

| Field | Meaning |
| --- | --- |
| `meal_role` | main, set, side, dessert, beverage, add_on, or unknown |
| `serves_min`, `serves_max` | documented range of people served by one listed unit; both null when unknown |
| `price.unit` | portion, piece, person, set, bottle, glass, pot, weight_100g, weight_kg, or unknown |
| `price.minimum_quantity` | smallest permitted number of price units; null when unknown |
| `price.channel` | dine_in, takeaway, delivery, or unknown |
| `review_status` | unreviewed, reviewed, quarantined |
| `review_reasons` | operator-readable codes retaining unresolved concerns |

`reviewed` does not establish halal status, ingredient absence or current opening.
Known contradictory text remains blocked even if a record is marked reviewed.
The meal planner currently accepts only reviewed main/set items with one-person
servings, portion/person/set pricing, minimum quantity one, confirmed dine-in
channel and full mandatory-charge totals. All other source and user checks still
apply. Supporting group allocation of shared dishes is separate unfinished work.

## Dated opening and kitchen service

The version-2 contract now accepts the following optional outlet fields. Older
deliveries still load; absent facts stay unknown. No received restaurant record
has been filled with guessed hours, holiday checks, or kitchen deadlines.

| Field | Meaning |
| --- | --- |
| `opening_hours[].last_order` | Latest local arrival/order time for this service interval, `HH:MM`; null when unknown |
| `opening_hours[].last_order_next_day` | Whether that clock is on the date after the interval opens |
| `opening_hours[].last_order_source_ids` | Evidence specifically supporting the kitchen cutoff; required for a known cutoff |
| `opening_exceptions` | Explicit local-date overrides, described below; defaults to an empty array |
| `opening_exceptions_coverage` | Inclusive `starts_on` / `ends_on` dates and `source_ids` showing which dates were checked for exceptions; null when unknown |

Each exception has `on_date`, `status`, `intervals`, and `source_ids`. Status is
`closed`, `published`, or `unknown`. Only `published` has intervals. Exception
intervals use `opens`, `closes`, `closes_next_day` and the same kitchen fields as
regular intervals, without `weekday`. A dated exception itself covers its date;
other dates require the explicit coverage range. An empty exception array alone
does **not** mean that holiday hours have been checked. The retained legacy
`holiday_exceptions_known` boolean no longer grants recommendation eligibility.

This is an invented illustration, not collected restaurant evidence:

```json
{
  "opening_hours": [{
    "weekday": 0,
    "opens": "20:00",
    "closes": "02:00",
    "closes_next_day": true,
    "last_order": "00:45",
    "last_order_next_day": true,
    "last_order_source_ids": ["operator-hours-confirmation"]
  }],
  "opening_exceptions_coverage": {
    "starts_on": "2026-10-01",
    "ends_on": "2026-10-31",
    "source_ids": ["operator-hours-confirmation"]
  },
  "opening_exceptions": [{
    "on_date": "2026-10-06",
    "status": "closed",
    "intervals": [],
    "source_ids": ["operator-hours-confirmation"]
  }]
}
```

The source ID must resolve to a normal catalog source with permitted display,
an observation time, and a future expiry. Source text must support the assertion:
seeing ordinary weekly hours on a page does not establish exception coverage or
a kitchen deadline. The parser validates shape, dates, source references, unique
exception dates and cutoffs inside their service interval. It cannot independently
verify the restaurant's statement. Do not derive `last_order` from `closes`.

Recommendation checks use the outlet timezone, the planned arrival (`meal_at`),
and the planned finish (`meal_at + duration_minutes`). Arrival must be at or
before last order; the whole meal must fit one uninterrupted published service
interval. An overnight interval can serve the following morning. A dated
override replaces that entire calendar date, including prior-night spillover:
in the illustration, Tuesday's closure ends Monday service at midnight, despite
the ordinary 02:00 closing time. Finishing exactly at midnight does not occupy
Tuesday. For an exceptional overnight opening, record the actual dates explicitly;
the engine does not join separate adjacent service intervals automatically.

Evidence for weekly hours, exception coverage, applicable exceptions and kitchen
deadlines must be usable both when checked and through the planned finish. A
known closure or missed cutoff excludes the outlet. Unknown coverage, unknown
kitchen deadlines or unusable supporting evidence require confirmation, never a
checked shortlist. Passing service evidence is included in the option's sources.
Generation and final selection both use this same deterministic check.

The same-order budget/dietary rule is also pinned by an acceptance fixture: an
outlet with an RM30 vegetarian dish and an RM10 chicken dish cannot satisfy a
vegetarian diner's RM15 cap. Lowering the vegetarian dish's full payable price
to RM14 makes that exact dish eligible; the cheap unsuitable dish never supplies
the displayed price. Tests use fictional reviewed dishes and source evidence.

## Loading and reviewing future deliveries

```bash
python -m dining.catalog.cli /path/to/catalog.real.json --upgrade \
  --install var/catalog.next.v2.json --report var/catalog.next.review.json
python webapp.py --catalog var/catalog.next.v2.json --db var/dining.sqlite3
```

Generic `--upgrade` never performs the delivery-specific channel corrections
above. New assertions must be reviewed against their own collector report and
source evidence. Fix facts in a separate reviewed copy, retain evidence, validate,
then restart to load that exact version. No unauthenticated source-editing or
"approve everything" browser endpoint is exposed.

The operational recommendation path stays structured and enumerates outlets
before menus. No Chroma collection was populated; the legacy prototype's Chroma
files remain separate. Source rights/freshness and reviewed content must be
resolved before building that new index. Vector similarity will not replace
price, dietary or order-allocation checks.
