# Golden recommendation tests — group fit and personal best fit

**Status:** DRAFT for review. **Not executed.** Awaiting product-owner approval before
a runner is written or any case is run.
**Date:** 7 October 2026
**Scope:** 100 deterministic cases that check whether the recommender returns the
*right* restaurants and menu items. Two outputs are checked:

1. **Group fit**: the shared shortlist (`result.options`) from `dining/recommendation/engine.py` and `dining/recommendation/ranking.py`.
2. **Personal best fit**: the private "Best fit for you" list (`my_personal_recommendations`) from `dining/recommendation/personal.py`.

## 1. Sources of truth

Expected outcomes come from the requirements, **not** from the current code. Where the
code and the requirements disagree, the case should fail.

| Source | Sections used |
|---|---|
| `restaurant-recommendations-prd/PRD.md` | §6.3 check-in semantics, §7.3 edge cases, §8.2 hard checks, §9.1–9.4 factors/formula/floor/diversity, §10.1–10.3 learning, §15.3 evaluation |
| `docs/SEMANTIC_RETRIEVAL_TICKETS.md` — **MAKAN-212** | Personal recommendations: group-eligible pool, per-diner item eligibility, privacy, backup/choose-separately/stale rules |
| `docs/PRD_ACCEPTANCE.md` | Current acceptance claims (REQ-06, REQ-10, REQ-11) |

The expected rankings were computed with a small, independent PRD §9 calculator. It
was written from the PRD text and not copied from `dining/recommendation/ranking.py`. The numbers can
be checked by hand with the cheat sheet in §4.

## 2. Golden fixture

All data is **fictional**. The catalog is `synthetic: true`. Every source is fresh: it
was observed 1 day ago and expires in 14 days. Rights allow display. Every outlet has
dated-exception coverage for ±30 days around the meal.

### 2.1 Default meal

| Field | Value |
|---|---|
| `meal_at` | Tomorrow 12:30 MYT (04:30Z) |
| `duration_minutes` | 60 |
| Meeting point | 3.1200, 101.6200 |
| `radius_km` | 5 |

### 2.2 Outlets (`GC-1` golden catalog)

Outlets lie due north of the meeting point, so distances are exact and distinct.

| ID | Name | Brand | Primary cuisine | Lat (lng 101.62) | Dist km | Halal evidence | Step-free entrance | Hours (every day) |
|---|---|---|---|---|---|---|---|---|
| G01 | Kedai Sup Harmoni | BR-SUP | Malaysian | 3.1227 | 0.3 | `certified`, fresh | unknown | 10:00–22:00, last order 21:30 |
| G02 | Rasa Kari | BR-KARI | Indian | 3.1254 | 0.6 | `unknown` | **accessible, reviewed, fresh** | 10:00–22:00, LO 21:30 |
| G03 | Wok & Noodle | BR-WOK | Chinese | 3.1281 | 0.9 | `not_halal` | unknown | 10:00–22:00, LO 21:30 |
| G04 | Ember Grill | BR-EMBER | Western | 3.1308 | 1.2 | `restaurant_claim` | unknown | 10:00–22:00, LO 21:30 |
| G05 | Trattoria Bunga | BR-TRAT | Italian | 3.1335 | 1.5 | `unknown` | **inaccessible, reviewed** | 10:00–22:00, LO 21:30 |
| G06 | Baan Thai | BR-BAAN | Thai | 3.1362 | 1.8 | `restaurant_claim` | unknown | 10:00–22:00, LO 21:30 |
| G07 | Mamak Bistari | BR-MAMAK | Mamak | 3.1389 | 2.1 | `certified`, fresh | unknown | 07:00–23:00, LO 22:30 |

### 2.3 Menu items

Unless stated otherwise, every item has these properties:

- `review_status=reviewed`, `live_availability=available`, `ingredients_complete=true`
- `meal_role=main`, `serves_min=serves_max=1`
- Price: `channel=dine_in`, `unit=portion`, `minimum_quantity=1`, `all_mandatory_charges_known=true`, and `payable_amount_minor` equal to the price shown

Attributes use only the `dining-tags-v1` ontology.

| Item | Outlet | Name | Attributes | RM | Dietary claims | Ingredients |
|---|---|---|---|---|---|---|
| I01 | G01 | Chicken noodle soup | noodle_soup, savoury, mild, light | 18.00 | — | chicken, noodles, egg, celery |
| I02 | G01 | Mixed vegetable soup | soup, light, none | 14.00 | vegetarian, vegan | cabbage, carrot, tofu, soy |
| I03 | G02 | Dhal rice set | rice, savoury, mild, hearty | 16.00 | vegetarian, vegan | rice, lentils, onion |
| I04 | G02 | Mutton curry rice | rice, rich, spicy, hot, hearty | 26.00 | — | mutton, rice, chilli, coconut |
| I05 | G03 | Wonton noodles | noodles, savoury, mild, regular | 15.00 | — | noodles, pork, prawn, egg, wheat |
| I06 | G03 | Tofu fried rice | rice, savoury, none, regular | 13.00 | vegetarian | rice, tofu, egg, soy |
| I07 | G04 | Grilled chicken chop | grill, smoky, none, hearty | 34.00 | — | chicken, potato, butter |
| I08 | G04 | Garden salad | salad, light, none | 24.00 | vegetarian | lettuce, parmesan, egg, wheat |
| I09 | G05 | Mushroom cream pasta | pasta, rich, none, regular | 29.00 | vegetarian | wheat, mushroom, milk, parmesan |
| I10 | G05 | Margherita pizza | pizza, savoury, none, regular | 31.00 | vegetarian | wheat, tomato, milk |
| I11 | G06 | Tom yum noodle soup | noodle_soup, sour, spicy, hot, regular | 21.00 | — | prawn, noodles, chilli, lemongrass |
| I12 | G06 | Pineapple fried rice | rice, sweet, mild, regular | 20.00 | — | rice, prawn, peanut, pineapple, egg |
| I13 | G07 | Roti canai with dhal | savoury, mild, light *(no dish tag)* | 7.00 | vegetarian | wheat, ghee, lentils |
| I14 | G07 | Mee goreng mamak | noodles, spicy, medium, regular | 11.00 | — | noodles, egg, chicken, chilli, tomato |
| I15 | G07 | Teh tarik | — | 4.00 | vegetarian | tea, milk — **`meal_role=drink`** |

### 2.4 Default diners

Each case changes only the fields it names. `P1`, `P2`, … are the included diners in
check-in order.

```yaml
profile:  {allergy_status: none, halal_policy: none, requirements_reviewed: true, dietary_requirements: []}
response: {requirements_confirmed: true, budget: 50}
```

### 2.5 Overlays (applied only when a case names them)

| Overlay | Change |
|---|---|
| OV-G08 | Add **G08** "Kedai Sup Harmoni — Branch 2". Same brand as G01 (BR-SUP), Malaysian, `certified`, lat 3.1245 (0.5 km). Menu I81/I82 is a copy of I01/I02. |
| OV-G09 | Add **G09** "Sup Utopia", BR-UTOPIA, Malaysian, lat 3.1740 (**6.0 km**). It has item I91 "Clear soup" [soup, light, none], RM10, vegan. |
| OV-HALAL-EXP | G01's halal certificate source `expires_at` = yesterday |
| OV-G03-CLOSED | G03 `opening_exceptions` on the meal date: `status=closed` (fresh source) |
| OV-G04-HRS-UNK | G04 `hours_status=unknown` |
| OV-G05-SRC-EXP | All G05 outlet/item sources: `expires_at` = yesterday |
| OV-G07-NIGHT | G07 hours become 18:00–03:00 (`closes_next_day`), last order 02:30 next day, every day |
| OV-Q-I05 | I05 `review_status=quarantined` |
| OV-UNAV-I14 | I14 `live_availability=unavailable` |
| OV-Q-I02 / OV-UNAV-I02 | I02 is quarantined / unavailable |
| OV-PLATTER | Add I16 at G01 "Family claypot soup" [soup, light, none], RM40, `unit=set`, `serves_min=2`, `serves_max=4`, vegan |
| OV-CHG-UNK | I13 and I14: `all_mandatory_charges_known=false`, `payable_amount_minor=null` |
| OV-DUP | Add 100 copies of I05 (new `item_id`s, identical otherwise) |
| OV-NOISE | Add 50 G01 items named "Unrelated cake n": no attributes, no cuisine tags, RM18, otherwise the same as I01 |
| PC(term) | `preparation_confirmations` for every outlet: `exact_bounded_claim` = "Kitchen confirmed the intended order is prepared without *term* and without shared *term* contact", `expires_at` = meal + 1 day |

## 3. Assertion vocabulary and pass rules

| Term | Meaning |
|---|---|
| `status` | Exact `result.status`: `shortlisted`, `needs_input`, `needs_verification` or `no_options` |
| `order` | **Exact ordered** outlet IDs of `result.options` |
| `items` | The menu item the ranker chose for each diner at that outlet: `G03: P1=I05, P2=I06`. Checked against `option.menu_items` (a set of item IDs). |
| `not in options` | The outlet ID is absent from `result.options`. It may appear in `verification`. |
| `excluded` | Absent from **both** `options` and `verification` (a known conflict, PRD §8.2) |
| `in verification` | Present in `result.verification` |
| `score` | Group `base_score` within ±0.0005. Public results drop `_score`, so the runner reads it through a test hook or recomputes it with `dining.recommendation.ranking.base_score` from the per-diner fits. |
| `personal Pn` | `my_personal_recommendations` for that diner, in rank order: `(outlet, item)` pairs |
| `personal Pn ∌ X` | Item X never appears in that diner's personal list |

**A case passes only if every listed assertion holds.** In every shortlisted case the
runner also checks these invariants. They are not repeated in each case:

- **INV-1:** at most 3 options, and no outlet appears twice.
- **INV-2:** every chosen item satisfies that diner's dietary claims, avoid list, firm cap and `meal_role ∉ {drink, dessert, side, beverage}`.
- **INV-3:** no shared field reveals another diner's private requirement, budget, score or personal list.
- **INV-4:** for every included diner, `my_personal_recommendations` contains only outlets in `result.options`, has 1 to 3 entries ranked 1..n, has scores in [0, 1], and every item passes INV-2 for **that** diner (MAKAN-212 AC 2–3).

## 4. Scoring cheat sheet (PRD §9.2–9.3)

```text
individual = .45C + .20H + .15T + .10B + .10O         unknown feature = .5
group      = .60·mean(individual) + .40·min(individual)
base       = .85·group + .10·Q(.5) + .05·N(.5 unless novelty intent)
floor: any individual < .35 → outlet not shortlisted
```

With only a craving given (all other features neutral), the values are:

| Craving result | C | Individual | Base when 2 diners are equal |
|---|---:|---:|---:|
| Exact attribute match | 1.0 | .725 | **.6913** |
| Broader match (noodle_soup ↔ soup/noodles) | .7 | .590 | **.5765** |
| No dish tag on item / no craving | .5 | .500 | **.5000** |
| Known mismatch | .2 | .365 | **.3852** |

Tie-breaks for equal scores, in order:

1. Shorter distance, then outlet ID.
2. Inside an outlet: higher fit, then lower price, then name.
3. Diversity: a different primary cuisine may take a slot if it is within .10 of the best remaining candidate (PRD §9.3).

---

## A. Hard eligibility gates (GT-001 – GT-024)

### GT-001 · Baseline: no taste input
- **Setup:** defaults.
- **Expect:** `status=shortlisted`; `order=[G01, G02, G03]`; items G01: I02/I02, G02: I03/I03, G03: I06/I06. Every score is .5000. `fit_confidence="limited"`.
- **Why:** All candidates tie at .5. Distance breaks the tie, and inside each outlet the cheapest item wins. No coverage means confidence is limited (§9.2, 60% rule).

### GT-002 · Outlet outside the search radius is never considered
- **Setup:** OV-G09; P1 and P2 `craving="soup"`.
- **Expect:** `order=[G01, G06, G07]`; G09 excluded; `examined_outlets=7`.
- **Why:** G09's soup is a perfect match, but it is 6 km away and the radius is 5 km (S04).

### GT-003 · Vegetarian diner: every assigned dish is vegetarian
- **Setup:** P1 `dietary_requirements=[vegetarian]`, `craving="noodles"`; P2 `craving="noodles"`.
- **Expect:** `order=[G07, G03, G01]`; G06 not in options. Items: G07 P1=I13, P2=I14; G03 P1=I06, P2=I05; G01 P1=I02, P2=I01. Scores .5574 / .4770 / .4426.
- **Why:** P1's noodle cravings (I01, I05, I14) are not vegetarian, so P1 gets a vegetarian main instead. G06 has no vegetarian main (§8.2, "a plausible suitable order for each person").

### GT-004 · Vegan is stricter than vegetarian
- **Setup:** P1 `dietary_requirements=[vegan]`.
- **Expect:** `order=[G01, G02]` (exactly 2 options); G03–G07 not in options.
- **Why:** I06, I08, I09, I10 and I13 are vegetarian but not vegan. A vegetarian claim must not satisfy vegan.

### GT-005 · One dish must meet both the diet and the budget
- **Setup:** P1 `dietary_requirements=[vegetarian]`, `budget=12`.
- **Expect:** `order=[G07]`; P1=I13.
- **Why:** Every other vegetarian main (I02, I03, I06, I08–I10) costs more than RM12. At G07, the cheap non-vegetarian I14 must not combine with a vegetarian dish to count as one valid order. The single dish I13 has to meet both requirements (PRD acceptance A08).

### GT-006 · Firm cap removes over-budget outlets
- **Setup:** P1 `budget=12`.
- **Expect:** `order=[G07]`; P1's item ∈ {I13, I14}.

### GT-007 · Cap boundary is inclusive and uses minor units
- **Setup (a):** P1 `budget=13`. **Setup (b):** P1 `budget=12.99`.
- **Expect (a):** `order=[G03, G07]`; G03 P1=I06 (RM13.00 passes). **Expect (b):** `order=[G07]`.

### GT-008 · A cheap drink is not a meal
- **Setup:** P1 `budget=5`.
- **Expect:** `status=no_options`; `options=[]`; I15 never chosen.
- **Why:** Teh tarik (RM4) is a drink. A drink cannot make an outlet affordable.

### GT-009 · Certified-halal policy accepts only current certificates
- **Setup:** P1 `halal_policy=certified`.
- **Expect:** `order=[G01, G07]`. G02–G06 not in options. G04/G06 (`restaurant_claim`) and G03 (`not_halal`) may appear in verification, but never as ordinary options.
- **Why:** A restaurant's claim, "no pork" or the cuisine is not certification (§8.2).

### GT-010 · An expired certificate becomes "Needs confirmation"
- **Setup:** OV-HALAL-EXP; P1 `halal_policy=certified`.
- **Expect:** `order=[G07]`; G01 in verification; G01 not in options.

### GT-011 · An unknown halal policy blocks generation
- **Setup:** P1 `halal_policy=unknown`. Repeat with `review`.
- **Expect:** `status=needs_verification`; `options=[]`.

### GT-012 · An unknown or withheld allergy state blocks generation
- **Setup:** P1 `allergy_status=unknown`. Repeat with `withheld`.
- **Expect:** `status=needs_verification`; `options=[]`; the explanation does not mention allergies or P1.

### GT-013 · A declared allergy with no preparation confirmation blocks generation
- **Setup:** P1 `allergy_status=declared`, `allergens=[peanut]`; no confirmations.
- **Expect:** `status=needs_verification`; `options=[]`; "peanut" does not appear anywhere in the shared result.
- **Why:** The menu alone never guarantees allergy safety (§8.2).

### GT-014 · Avoid peanut with confirmations: the peanut dish is never assigned
- **Setup:** PC(peanut); P1 `avoid=[peanut]`, `craving="rice"`; P2 `craving="rice"`.
- **Expect:** `order=[G02, G03, G07]`; I12 never chosen for P1; scores .6913 / .6913 / .5000.

### GT-015 · Avoid pork with confirmations
- **Setup:** PC(pork); P1 `avoid=[pork]`, `craving="noodles"`; P2 `craving="noodles"`.
- **Expect:** `order=[G07, G01, G06]`; I05 never chosen for P1; G03's score (.4770, P1 can only have I06) is below G06's.

### GT-016 · An expired preparation confirmation blocks only that outlet
- **Setup:** PC(peanut), but G07's confirmation expires 1 hour **before** the meal. P1 `avoid=[peanut]`, `craving="noodles"`; P2 `craving="noodles"`.
- **Expect:** `order=[G03, G01, G06]`; G07 in verification; G07 not in options.

### GT-017 · Required step-free access
- **Setup:** P1 `accessibility_requirements=[step_free_entrance]`.
- **Expect:** `order=[G02]`. G05 is **excluded** (known inaccessible). G01, G03, G04, G06 and G07 are in verification (unknown).
- **Why:** PRD §8.2: "A known conflict is excluded. Unknown critical evidence appears in a separate Needs confirmation panel."

### GT-018 · A known closure on the date removes the outlet
- **Setup:** OV-G03-CLOSED; P1 and P2 `craving="noodles"`.
- **Expect:** `order=[G07, G01, G06]`; G03 excluded (not in verification).

### GT-019 · Unknown hours become "Needs confirmation", not an option
- **Setup:** OV-G04-HRS-UNK; P1 `craving="grill"`; P2 `craving="salad"`.
- **Expect:** G04 not in options; G04 in verification; `order=[G07, G01, G02]`.

### GT-020 · Late meal after most kitchens' last order
- **Setup:** `meal_at` = tomorrow 22:15 MYT; `duration_minutes=45`.
- **Expect:** `order=[G07]`; P1=P2=I13.
- **Why:** G01–G06 close at 22:00. G07 has its last order at 22:30 and closes at 23:00, which leaves enough time.

### GT-021 · Overnight service is honoured
- **Setup:** OV-G07-NIGHT; `meal_at` = the day after tomorrow, 01:00 MYT.
- **Expect:** `order=[G07]`; every other outlet is closed.
- **Why:** The previous day's 18:00–03:00 interval covers 01:00 (§15.3 "overnight service").

### GT-022 · Expired source evidence is not published
- **Setup:** OV-G05-SRC-EXP; P1 `craving="pasta"`; P2 `craving="pizza"`.
- **Expect:** G05 not in options; `order=[G07, G01, G02]`.

### GT-023 · Quarantined and unavailable dishes are never chosen
- **Setup:** OV-Q-I05 + OV-UNAV-I14; P1 and P2 `craving="noodles"`.
- **Expect:** `order=[G01, G06, G07]`; I05 and I14 never appear; G07 P1=P2=I13.

### GT-024 · A shared platter is never split into a per-person price
- **Setup:** OV-PLATTER; P1 and P2 `craving="soup"`.
- **Expect:** `order=[G01, G06, G07]`; G01 items are P1=P2=I02; I16 never appears.

## B. Session status and conflict messaging (GT-025 – GT-029)

### GT-025 · Only one person checked in
- **Setup:** P1 only.
- **Expect:** `status=needs_input`; `options=[]`.
- **Why:** §7.3: a session reduced to one person is not a group recommendation.

### GT-026 · No shared meeting point
- **Setup:** meal `latitude=null`, `longitude=null`.
- **Expect:** `status=needs_input`; `options=[]`.

### GT-027 · Requirements not reviewed
- **Setup:** P2 `requirements_reviewed=false`. Repeat with response `requirements_confirmed=false`.
- **Expect:** `status=needs_verification`; `options=[]`.

### GT-028 · No overlap: explained without naming anyone
- **Setup:** PC(wheat); P1 `budget=8`; P2 `dietary_requirements=[vegetarian]`, `avoid=[wheat]`.
- **Expect:** `status=no_options`; `options=[]`; the explanation names neither P1/P2 nor "wheat", "vegetarian" or "RM8".
- **Why:** §7.3: "Explain which kinds of constraints conflict without identifying private owners." The only main under RM8 is I13, which contains wheat.

### GT-029 · An unknown all-in price is "needs verification", not "no options"
- **Setup:** OV-CHG-UNK; P1 `budget=12`.
- **Expect:** `status=needs_verification`; `options=[]`.
- **Why:** P1's only affordable mains have unknown charges. Unknown is not the same as fail (§8.2).

## C. Group fit scoring and ranking (GT-030 – GT-063)

### GT-030 · Both diners crave noodles
- **Setup:** P1 and P2 `craving="noodles"`.
- **Expect:** `order=[G03, G07, G01]`; items I05, I14, I01; scores .6913 / .6913 / .5765.
- **Why:** Exact noodle matches come first. The noodle_soup match is "broader" (.7). G01 beats G06 at the same score because it is closer.

### GT-031 · Exact match beats broader match inside one outlet
- **Setup:** P1 and P2 `craving="soup"`.
- **Expect:** `order=[G01, G06, G07]`; G01 items P1=P2=**I02** (exact `soup`), not I01; scores .6913 / .5765 / .5000.

### GT-032 · Both crave rice
- **Setup:** P1 and P2 `craving="rice"`.
- **Expect:** `order=[G02, G03, G06]`; items I03, I06, I12; all .6913.

### GT-033 · Both want spicy
- **Setup:** P1 and P2 `craving="spicy"`.
- **Expect:** `order=[G02, G06, G07]`; items I04, I11, I14.

### GT-034 · One outlet satisfies two different cravings with different dishes
- **Setup:** P1 `craving="pasta"`; P2 `craving="pizza"`.
- **Expect:** `order[0]=G05`, with P1=I09 and P2=I10; full order `[G05, G07, G01]`.

### GT-035 · Noodles vs rice
- **Setup:** P1 `craving="noodles"`; P2 `craving="rice"`.
- **Expect:** `order=[G03, G06, G07]`; G03 P1=I05, P2=I06; scores .6913 / .6109 / .5574.

### GT-036 · Grill vs salad
- **Setup:** P1 `craving="grill"`; P2 `craving="salad"`.
- **Expect:** `order[0]=G04`, with P1=I07 and P2=I08; score .6913.

### GT-037 · "Anything" is a valid neutral answer
- **Setup:** P1 and P2 `craving="anything"`.
- **Expect:** `status=shortlisted`; `order=[G01, G02, G03]` (same as GT-001). C has coverage 1, but the craving does not change the ranking.

### GT-038 · A negated craving is not turned into a preference
- **Setup:** P1 and P2 `craving="no noodles"`.
- **Expect:** `order=[G01, G02, G03]` (same as GT-001). G03 and G07 are **not** boosted.

### GT-039 · Today's cuisine choice
- **Setup:** P1 and P2 `cuisines=["Thai"]`.
- **Expect:** `order[0]=G06`, with item I12 (an exact cuisine match, and cheaper than I11); score .6913.

### GT-040 · Two different cuisine wishes get a fair split
- **Setup:** P1 `cuisines=["Thai"]`; P2 `cuisines=["Indian"]`.
- **Expect:** `order[0:2]=[G02, G06]`, each .4770; G02 is first because it is closer.

### GT-041 · Today's chilli level: hot
- **Setup:** P1 and P2 `spice="hot"`.
- **Expect:** `order=[G02, G06, G01]`; G02 item I04; G06 item I11.

### GT-042 · Today's chilli level: none
- **Setup:** P1 and P2 `spice="none"`.
- **Expect:** `order=[G01, G03, G04]`; G01 I02, G03 I06, G04 I08. G05 also scores .6913 but is the 4th closest.

### GT-043 · "Noodle soup" is understood as one dish family
- **Setup:** P1 and P2 `craving="noodle soup"`.
- **Expect:** `order=[G01, G06, G03]`; G01 items P1=P2=**I01** (not the vegetable soup I02); G06 I11.
- **Why:** §9.2: an exact desired dish/category match scores 1. "Noodle soup" is the curated `noodle_soup` category, so I01 and I11 are exact matches. Plain soup or plain noodles are only broader matches.

### GT-044 · Bahasa Melayu craving terms
- **Setup:** P1 and P2 `craving="mee pedas"`.
- **Expect:** `order=[G07, G06, G02]`; G07 item I14 (`mee`→noodles, `pedas`→spicy).

### GT-045 · Light appetite
- **Setup:** P1 and P2 `appetite="light"`.
- **Expect:** `order=[G01, G04, G07]`; items I02, I08, I13; each .5425.

### GT-046 · Hearty appetite
- **Setup:** P1 and P2 `appetite="hearty"`.
- **Expect:** `order=[G02, G04, G01]`; G02 I03, G04 I07.

### GT-047 · Soft target with a firm cap
- **Setup:** P1 `soft_budget_target=15`, `budget=30`, `craving="rice"`; P2 `craving="rice"`.
- **Expect:** `order=[G03, G02, G06]`, scores .7040 / .7023 / .6955. G05 not in options: P1's best fit there is .3217, below the floor.
- **Why:** B = 1 when the price is at or below the target, then falls linearly to 0 at the cap. The cheaper exact rice match therefore wins.

### GT-048 · Flexible budget (target, but no firm cap)
- **Setup:** P1 `soft_budget_target=15`, **no** `budget` and no profile `max_budget`, `craving="noodles"`; P2 `craving="noodles"`.
- **Expect:** `status=shortlisted`; `order=[G03, G07, G01]`; P1's B at G01 = 15/18 = .8333; G01 score .5850.
- **Why:** M03 offers "Flexible", and §9.2 says "When no firm budget cap exists, use B = min(1, target/price)". A missing firm cap must not block the meal.

### GT-049 · Travel time (shared route estimates)
- **Setup:** Both diners: `craving="rice"`, `comfortable_travel_minutes=20`, `route_estimates` ETA minutes {G01: 5, G02: 5, G03: 30, G06: 10, G07: 18}, all fresh.
- **Expect:** `order=[G02, G06, G03]`; scores .7231 / .6913 / .6275.
- **Why:** T = 1 − min(ETA/20, 1). G03's 30-minute ETA gives T = 0.

### GT-050 · An expired route estimate is neutral, not fast
- **Setup:** Same as GT-049, but ETAs are {G02: 18, G03: 2 (**expired**), G06: 2}.
- **Expect:** `order=[G06, G03, G02]`; scores .7423 / .6913 / .6403. G03 gets T = .5, not .9.

### GT-051 · A lasting cuisine preference applies when today is silent
- **Setup:** P1 profile `cuisines=["Thai"]`; no cravings.
- **Expect:** `order=[G06, G01, G02]`; G06 score .5255.

### GT-052 · Today's answer overrides lasting taste in the same dimension
- **Setup:** P1 profile `cuisines=["Thai"]`, response `cuisines=["Indian"]`.
- **Expect:** `order[0]=G02` (.5574). G06 scores the same as the other non-Indian outlets (.4197) and gets no Thai boost.

### GT-053 · Three diners with three different cravings
- **Setup:** P1 `craving="noodles"`; P2 `craving="soup"`; P3 `craving="rice"`.
- **Expect:** `order=[G06, G07, G03]`; G06 items P1=I11, P2=I11, P3=I12; scores .5995 / .5383 / .5076.

### GT-054 · Eight diners (pilot maximum) all crave noodles
- **Setup:** P1–P8 `craving="noodles"`.
- **Expect:** Identical to GT-030. Group size alone must not change the ranking.

### GT-055 · Order and organizer role have no effect
- **Setup:** GT-035 with P1 and P2 swapped, then again with P2 as the meal organizer.
- **Expect:** `order=[G03, G06, G07]` and the same scores. Each diner's item assignment follows the diner, not the position.
- **Why:** §9.3: "Each participant has equal influence; organizer status … add[s] no weight."

### GT-056 · Duplicated or irrelevant menu rows do not change the ranking
- **Setup (a):** OV-DUP with GT-030. **Setup (b):** OV-NOISE with GT-031.
- **Expect:** (a) the same order, items and scores as GT-030; (b) the same as GT-031, and no "Unrelated cake" item is chosen.

### GT-057 · PRD §9.4 worked example (formula unit check)
- **Setup:** Call `dining.recommendation.ranking.base_score` / `group_fit` directly with fits A (.95, .85, .20), B (.75, .75, .70), C (.80, .60, .65); Q = N = .5.
- **Expect:** A group fit = .4800 and min .20 < .35 (blocked). B base = .6870. C base = .6275. B ranks above C.

### GT-058 · Learned venue affinity, and memory turned off
- **Setup (a):** P1 `memory_enabled=true` with 2 `confirmed_visit` observations at G06 (enjoyed, 1 day old); no cravings. **Setup (b):** the same, with `memory_enabled=false`.
- **Expect (a):** `order=[G06, G01, G02]`; G06 score .5127, because P1's venue H = (1 + 2)/(2 + 2) = .75. **Expect (b):** the same as GT-001.

### GT-059 · A confirmed "would not repeat" overrides learned enjoyment
- **Setup:** GT-058(a), plus a venue preference for P1 at G06 with `would_repeat=false`; P1 and P2 `craving="spicy"`.
- **Expect:** `order=[G02, G07, G06]`; G06 score .6318, below the .6913 it scores in GT-033.

### GT-060 · Explore prefers unvisited places only when visit history is complete
- **Setup:** Both diners: `novelty="explore"`, `memory_enabled=true`, `visit_history_complete=true`, and one confirmed visit to G01.
- **Expect:** `order=[G02, G03, G04]`; G01's score is .5132 and the others are .5250.
- **Variant:** with `visit_history_complete=false`, unvisited outlets get N = .5 (score .5000). G01's enjoyed visit still lifts its lasting venue taste, so `order=[G01, G02, G03]`.
- **Correction (7 Oct 2026):** the first draft left out the learned venue liking (H = .6658) that an enjoyed, consented visit adds under PRD §9.2.

### GT-061 · Familiar rewards a liked visit, never a disliked one
- **Setup (a):** Both `novelty="familiar"`, memory on, an enjoyed visit at G05. **Setup (b):** the same, but the visit was "did_not_enjoy".
- **Expect (a):** `order=[G05, G01, G02]`; G05 score .5532. **Expect (b):** `order=[G01, G02, G03]`; G05 score .4568.
- **Correction (7 Oct 2026):** scores now include the learned venue liking from the visit (enjoyed .6658, did not enjoy .3342).

### GT-062 · 90-day half-life on venue feedback
- **Setup:** P1 memory on. One enjoyed visit at G04 (1 day old) and one enjoyed visit at G06 (360 days old).
- **Expect:** `order=[G04, G06, G01]`; scores .5085 / .5008 / .5000.

### GT-063 · Learning never relaxes a hard requirement
- **Setup:** P1 `halal_policy=certified`, memory on, 5 enjoyed visits at G03 (`not_halal`).
- **Expect:** `order=[G01, G07]`; G03 not in options.
- **Why:** §10.2: "No learning-based suggestion changes … religious dietary policy or other hard requirements."

## D. Fairness floor (GT-064 – GT-068)

### GT-064 · One diner's very poor fit blocks another diner's favourite
- **Setup:** P1 `craving="pasta"`, `taste_preferences={cuisine:thai: dislike, flavour:sour: dislike, spice:hot: dislike}`; P2 `craving="spicy sour"`.
- **Expect:** G06 not in options (P1's fit there is .265, below .35). `order=[G07, G02, G05]`.

### GT-065 · A majority favourite cannot override the floor
- **Setup:** P1 and P2 `craving="spicy sour"`; P3 has GT-064's P1 profile and `craving="pasta"`.
- **Expect:** G06 not in options, even though two of three diners score .725 there. `order=[G07, G02, G05]`; scores .5765 / .5076 / .4464.

### GT-066 · Every candidate below the floor
- **Setup:** `radius_km=1.0` (G01–G03 only). P1 `craving="pizza"` and dislikes the Malaysian, Indian and Chinese cuisines. P2 `craving="anything"`.
- **Expect:** `status=needs_input`; `options=[]`; the explanation asks for preference changes and names nobody.

### GT-067 · The mean/min blend prefers a compromise
- **Setup:** P1 `craving="soup"`; P2 `craving="grill"`.
- **Expect:** `order=[G07, G01, G04]`. G07 (.5000, fits .5/.5) beats G01 and G04 (.4770 each, fits .725/.365).
- **Why:** The 40% weight on the minimum fit rewards the option that does not leave one person poorly served. Reviewers should confirm this outcome is acceptable: G07 wins only because I13 has no dish tag, so it is neutral (see §6, Q3).

### GT-068 · Floor boundary
- **Setup:** Unit-level. Two outlets: one with individual fits (.35, .90) and one with (.3499, .90).
- **Expect:** The first is eligible and the second is rejected as `fit_floor` (strict `<` .35).

## E. Diversity and branch de-duplication (GT-069 – GT-074)

GT-070 to GT-073 call `dining.recommendation.ranking.rank_diverse` directly. Each candidate row has
the fields `outlet_id`, `brand_id`, `cuisines`, `distance_km` and `_score`.

### GT-069 · A second branch of the same brand at a similar distance is de-duplicated
- **Setup:** OV-G08; P1 and P2 `craving="soup"`.
- **Expect:** `order=[G01, G06, G07]`; G08 not in options, although it scores .6913, the same as G01.

### GT-070 · A branch at least 1 km closer is kept
- **Setup:** Rows X1 (brand X, 3.0 km, .80), X2 (brand X, 1.5 km, .75), Y (brand Y, 1.0 km, .50).
- **Expect:** `[X1, X2, Y]`.

### GT-071 · A different cuisine within .10 is promoted
- **Setup:** A (Malaysian, .80), B (Malaysian, .78), C (Thai, .72), all of different brands.
- **Expect:** `[A, C, B]`.

### GT-072 · A different cuisine more than .10 behind is not promoted
- **Setup:** A (Malaysian, .80), B (Malaysian, .78), C (Thai, .60).
- **Expect:** `[A, B, C]`.

### GT-073 · Deterministic tie-break
- **Setup:** Three rows with equal scores and equal cuisines, at distances 2.0, 1.0 and 1.0 km, with outlet IDs "b" and "a" for the two 1.0 km rows.
- **Expect:** `["a", "b", 2.0 km row]`.

### GT-074 · No more than 3 options, all unique
- **Setup:** Defaults (7 outlets are eligible).
- **Expect:** `len(options)=3`; the outlet IDs are unique; the brand IDs are unique.

## F. Personal best fit — MAKAN-212 (GT-075 – GT-100)

The expected personal ranking uses each diner's **own** PRD individual fit, with no
group-fairness term (MAKAN-212, "Personal score"). The chosen item must be the best
item that is eligible **for that diner**. A ✱ marks a case where only the item, not the
top-1 outlet, is asserted, because two outlets tie.

### GT-075 · The personal list comes from the group-eligible pool
- **Setup:** GT-035.
- **Expect:** P1 and P2 each have exactly 3 personal recommendations; each set of outlets = {G03, G06, G07}; ranks are 1, 2, 3.

### GT-076 · A clear personal best fit
- **Setup:** P1 `craving="noodles"`, `cuisines=["Chinese"]`; P2 `craving="rice"`.
- **Expect:** Group `order=[G03, G06, G07]`.
  - Personal P1 = [(G03, I05), (G07, I14), (G06, I11)].
  - P2 rank 1 ∈ {(G03, I06), (G06, I12)} ✱, and rank 3 = (G07, I13).
- **Why:** P1's personal order differs from the group order. The personal list follows P1's own fit.

### GT-077 · Different diners get different "Best fit for you" results
- **Setup:** P1 `halal_policy=certified`, `craving="soup"`; P2 `craving="noodles"`.
- **Expect:** Group `order=[G01, G07]`. Personal P1 rank 1 = (G01, I02); personal P2 rank 1 = (G07, I14).

### GT-078 · A personal item is the same as the group's item for that diner
- **Setup:** GT-076.
- **Expect:** At every outlet, the personal item equals that diner's item in the shared option: G03 P1=I05, P2=I06; G06 P1=I11, P2=I12; G07 P1=I14, P2=I13.

### GT-079 · A vegetarian diner never gets a meat dish
- **Setup:** GT-003.
- **Expect:** Personal P1 items are G07=I13, G03=I06, G01=**I02**; personal P1 ∌ I01, I05, I14.
- **Why:** I01 "Chicken noodle soup" matches the craving but is not vegetarian (MAKAN-212 AC 3).

### GT-080 · A vegan diner gets only vegan dishes
- **Setup:** P1 `dietary_requirements=[vegan]`, `craving="rice"`; P2 `craving="rice"`.
- **Expect:** Group `order=[G02, G01]`. Personal P1 = [(G02, I03), (G01, I02)].

### GT-081 · The avoid list applies to personal items
- **Setup:** PC(peanut); P1 `avoid=[peanut]`, `craving="rice"`, `cuisines=["Thai"]`; P2 `cuisines=["Thai"]`.
- **Expect:** Group `order=[G06, G02, G03]`; G06 P1=I11. Personal P1 at G06 = **I11**; personal P1 ∌ I12.
- **Why:** I12 is the best rice + Thai match, but it contains peanut.

### GT-082 · Avoid pork applies to personal items
- **Setup:** PC(pork); P1 `avoid=[pork]`, `craving="noodles"`; P2 `craving="rice"`, `cuisines=["Chinese"]`.
- **Expect:** Group `order=[G06, G07, G03]`. Personal P1 = [(G07, I14), (G06, I11), (G03, I06)]; personal P1 ∌ I05.

### GT-083 · The firm cap applies to personal items
- **Setup:** P1 `budget=15`, `craving="noodles"`; P2 `craving="noodles"`.
- **Expect:** Group `order=[G03, G07, G01]`. Personal P1 at G01 = **I02** (RM14), never I01 (RM18 > RM15).

### GT-084 · A certified-halal diner gets only certified outlets
- **Setup:** GT-077.
- **Expect:** P1's personal outlets ⊆ {G01, G07}.

### GT-085 · A drink is never a personal meal
- **Setup:** P1 `craving="teh tarik"`; P2 `craving="noodles"`.
- **Expect:** G07 is in the group order. Personal P1 at G07 ∈ {I13, I14}; personal P1 ∌ I15.

### GT-086 · A quarantined or unavailable item is never a personal pick
- **Setup (a):** OV-Q-I02. **Setup (b):** OV-UNAV-I02. P1 and P2 `craving="soup"`.
- **Expect:** In both, G01 is in the group order with item I01, and no personal list contains I02.

### GT-087 · Light appetite picks the light dish
- **Setup:** P1 `appetite="light"`; P2 `craving="grill"`.
- **Expect:** Group `order=[G04, G07, G01]`. Personal P1 at G04 = **I08**, not I07. P1's three personal fits are equal at .55 ✱. `LIGHT_MEAL_MATCH` appears only on items tagged `light`.

### GT-088 · The chilli level picks the right dish in a multi-dish outlet
- **Setup:** P1 `spice="hot"`; P2 `craving="rice"`.
- **Expect:** Group `order=[G02, G06, G03]`. Personal P1 at G02 = I04 and at G06 = I11 (not I03/I12) ✱.

### GT-089 · Today's request outranks lasting taste in the personal list
- **Setup:** P1 profile `cuisines=["Thai"]`, `craving="pasta"`; P2 `craving="spicy"`.
- **Expect:** Group `order=[G07, G06, G02]`. Personal P1 = [(G07, I13), (G06, I12), (G02, I03)].
- **Why:** At G06, P1's fit is .465: the Thai cuisine matches (H = 1) but the dish does not match pasta (C = .2). At G07, the fit is .5 because the dish is unknown (neutral). Today's dish request carries more weight (.45 against .20).

### GT-090 · A negated craving gives no craving credit
- **Setup:** P1 `craving="no noodles"`; P2 `craving="noodles"`.
- **Expect:** Group `order=[G03, G07, G01]`. Personal P1 items are G03=I06, G07=I13, G01=I02. No P1 recommendation has `CRAVING_MATCH`.

### GT-091 · No fuzzy false positives
- **Setup:** P1 and P2 `craving="sour"`.
- **Expect:** Group `order=[G06, G01, G02]`. Personal P1 rank 1 = (G06, I11). The G01 and G02 entries **do not** have `CRAVING_MATCH`.
- **Why:** "sour" ≠ "soup". Only curated attributes count as a match (§9.2).

### GT-092 · Reason codes are allowlisted and true
- **Setup:** GT-076.
- **Expect:**
  - Every code ∈ {GROUP_ELIGIBLE_POOL, CRAVING_MATCH, DISH_FAMILY_MATCH, FAVOURITE_CUISINE_MATCH, WITHIN_COMFORT_BUDGET, LIGHT_MEAL_MATCH}.
  - `WITHIN_COMFORT_BUDGET` ⇔ the item's all-in payable price ≤ the diner's budget.
  - `FAVOURITE_CUISINE_MATCH` appears for P1 only at G03.
  - `CRAVING_MATCH` appears only when the item's attributes match the craving: P1 at G03 and G07 (noodles); P2 at G03 and G06 (rice). It does not appear for P1 at G06, where noodle_soup is only a broader match.

### GT-093 · A diner with no input gets a neutral, stable list
- **Setup:** Defaults.
- **Expect:** Personal P1 = [(G01, I02), (G02, I03), (G03, I06)], the same as the group order, with all scores equal.

### GT-094 · Personal ranking is deterministic
- **Setup:** Run GT-076 twice, then once more with the diners' check-in order reversed.
- **Expect:** Identical personal lists (outlet, item, rank, score, reason codes) for each user ID in all three runs.

### GT-095 · Same outlet, different personal dish
- **Setup:** GT-034.
- **Expect:** Personal P1 rank 1 = (G05, I09); personal P2 rank 1 = (G05, I10).

### GT-096 · Privacy of personal results
- **Setup:** GT-076, viewed through the API (`GET /api/meals/{id}`) as P1 and as P2.
- **Expect:**
  - P1's view contains only P1's `my_personal_recommendations`.
  - Neither view contains the other diner's outlet/item list, scores or reason codes.
  - `result` has no `scores`, `personal` or `reason_codes` keys.
  - An operations/trace export contains no personal ranking.

### GT-097 · Saving a backup changes nothing shared
- **Setup:** GT-076. P2 calls `save_backup` on rank 1.
- **Expect:** Meal revision, status, votes, participant count and `result.options` are unchanged. P2's rank 1 status = `saved_backup`. No learning observation is created.

### GT-098 · Stale personal results are not reused
- **Setup:** GT-076. P1 then edits their check-in to `craving="rice"`, which increments the revision.
- **Expect:** P1's revision-n results are not returned. An action (`save_backup`) on the old revision is rejected with a revision conflict. After regeneration, P1's rank 1 ∈ {(G03, I06), (G02, I03), (G06, I12)}.

### GT-099 · Choosing separately before the group decides
- **Setup:** GT-076, before any final selection. P2 calls `choose_separately`.
- **Expect** (MAKAN-212, "Before final group selection"):
  - P2 is withdrawn from the decision denominator.
  - The meal revision increments.
  - The shortlist, votes and personal results are invalidated.
  - The meal needs to be regenerated for the new participant set.

### GT-100 · Dismissing or choosing elsewhere teaches no false dislike
- **Setup:** GT-076. P1 dismisses rank 2. After the group selects G03, P2 calls `choose_separately` on (G06, I12) and later confirms the visit as "enjoyed".
- **Expect:**
  - The dismiss creates no observation.
  - G03's shared decision history is preserved.
  - P2 has no observation, dislike or visit at G03.
  - The feedback is attached only to G06/I12.

## H. Majority preference (GT-101 – GT-104)

Added 7 October 2026 at the product owner's request. When most of the group wants the same thing, the top choice should serve that majority, unless a hard requirement or the fairness floor says otherwise.

### GT-101 · Two of three want Thai
- **Setup:** P1 and P2 `cuisines=["Thai"]`; P3 `cuisines=["Indian"]`.
- **Expect:** `order=[G06, G02, G01]`; G06 .5076, G02 .4464.
- **Why:** At Baan Thai the fits are .725, .725 and .365 → .6 × .605 + .4 × .365 = .509 → .5076. Rasa Kari is the reverse (.365, .365, .725) → .4464.

### GT-102 · A majority flips the compromise
- **Setup:** P1 and P2 `craving="soup"`; P3 `craving="grill"`.
- **Expect:** `order=[G01, G07, G06]`; G01 .5076, G07 .5000, G06 .4617.
- **Why:** With one soup and one grill fan (GT-067), the neutral compromise G07 wins. With two soup fans, the soup restaurant overtakes it.

### GT-103 · Three of four
- **Setup:** P1–P3 `craving="pasta"`; P4 `craving="rice"`.
- **Expect:** `order[0]=G05`; G05 .5230, G07 .5000.

### GT-104 · A majority can't override the fairness floor
- **Setup:** P1 and P2 `craving="spicy"`; P3 `craving="pasta"` and dislikes Thai, sour, hot and spicy.
- **Expect:** G06 not in options (P3's fit there is .265, below .35). `order=[G07, G02, G05]`; scores .5765 / .5076 / .4464.

---

## 5. Proposed runner (after approval)

- `tests/golden/golden_catalog.py` builds GC-1 and the overlays from §2. It reuses the freshness pattern of `ready_catalog()` in `tests/test_recommendation.py`.
- `tests/golden/test_golden_recommendations.py` holds one parametrized pytest per GT-ID.
  - Group and status cases call `Recommender(catalog)(snapshot)` directly.
  - Personal cases call `score_personal_options()` on the shortlisted options.
  - Lifecycle and privacy cases (GT-096 – GT-100) go through `create_app` + `TestClient`, like `tests/test_personal_recommendations.py`.
- Scores: a test-only hook records `_score` before `rank_diverse` drops it. No production behaviour changes.
- Output: a pass/fail table by GT-ID, plus a diff of expected and actual order/items for each failure. The runner will **not** change expectations to make tests pass. A failing case is reported for a product decision.

Run command once approved:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/golden -q -rA
```

## 6. Questions for the reviewer before running

1. **Personal pool scope.** The code builds personal results only from the ≤3 shortlisted options. MAKAN-212 defines the pool as *all* outlets that passed the group's hard checks. INV-4 currently asserts the narrower reading. Should a 4th eligible outlet that is a diner's best fit appear in their personal list?
2. **Avoid lists vs allergies.** The code treats any `response.avoid` entry as allergy-grade and requires a preparation confirmation. PRD O07 ("avoid pork / alcohol", must-avoid) is an ingredient rule, not an allergy. GT-015 and GT-082 add PC(pork) so they test the current flow. Should "avoid pork" require a kitchen confirmation?
3. **Neutral beats mismatch (GT-067).** The PRD's neutral .5 for an untagged dish (I13) lets it beat a dish that is a real match for one diner. This follows the PRD as written. Confirm it is the intended outcome.
4. **Known inaccessibility (GT-017).** This case expects G05 to be fully *excluded* (PRD: "a known conflict is excluded"), not listed under "Needs confirmation".

## 7. Cases most likely to fail (from reading the code; not run)

These are predictions to help review, not results.

| Case(s) | Why the current code may fail |
|---|---|
| GT-043 | The craving parser splits "noodle soup" into separate words, so I02 (soup) gets C = 1 and I01 gets C = .7 |
| GT-048 | `recommendation.py` adds "A private meal budget needs confirmation" when no firm cap exists |
| GT-017 | Known-inaccessible outlets are added to `verification` instead of being excluded |
| GT-079, 081, 083, 085, 086(b) | `score_personal_options` does not re-check the diner's diet, avoid list, firm cap, `meal_role` or availability for each item |
| GT-090, 091 | The personal craving match uses substring and fuzzy text (`difflib` ≥ .75), so "no noodles" matches noodles and "sour" matches "soup" |
| GT-092 | `DISH_FAMILY_MATCH` is never produced, and `CRAVING_MATCH` comes from text rather than curated attributes |
| GT-076, 089 | The personal score is a different heuristic (0.5 + bonuses), not the individual fit, so the ordering may differ |
| GT-099 | `choose_separately` only updates the personal row's status. No withdrawal or revision change was found. |
