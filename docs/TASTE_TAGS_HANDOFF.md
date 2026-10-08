# Handoff — taste tags and meal roles for the 58 verified outlets

**Created:** 7 October 2026
**Tool:** `scripts/taste_tags.py` (export → validate → stats → apply)
**Why:** On the real catalog, only 2 of 1,074 items have taste tags, and every item has
`meal_role=unknown`. As a result:

- The PRD craving score is neutral for every dish, so after semantic retrieval the ranking falls back to distance.
- Drinks, sides and desserts can be chosen as someone's meal.

Product decisions confirmed on 7 Oct 2026 (`docs/GOLDEN_PRODUCTION_58_TESTS.md` §5):
a drink, side or dessert is **never** a meal, and **relevance beats distance**. Both depend on
this data. The production golden cases most affected are P-001–P-030, P-061, P-079 and P-086.

## Paste this into the new session

> You are enriching menu data for the Group Dining app in
> `/Users/johnathanjohnathan/Documents/restaurant-recommendations`. Read
> `docs/TASTE_TAGS_HANDOFF.md` in full first, then follow it exactly.
>
> Goal: give each of the 1,073 reviewed menu items at the 58 verified outlets a `meal_role` and
> taste `attributes` from the existing `dining-tags-v1` ontology. Use
> `scripts/taste_tags.py` only. Do not edit the source catalog, the ranking code, the vector
> index or any test. Work outlet by outlet. Mark every entry `needs_review`, and stop for my
> approval before anything is set to `approved`, applied or re-indexed.

## Paths

| What | Path |
|---|---|
| Source catalog (read-only) | `var/catalog-import/kl-selangor-real-pilot-58/catalog.validated.json` |
| Activation manifest (58 outlet IDs) | `var/catalog-import/kl-selangor-real-pilot-58/activation-manifest.json` |
| Patch to fill | `data/enrichment/taste-tags.patch.json` |
| Output catalog (only after approval) | `var/catalog-import/kl-selangor-real-pilot-58-tagged/catalog.validated.json` |
| Ontology source of truth | `dining/recommendation/ranking.py` → `DIMENSIONS`, `ONTOLOGY_VERSION` |

## Steps

```bash
C=var/catalog-import/kl-selangor-real-pilot-58/catalog.validated.json
M=var/catalog-import/kl-selangor-real-pilot-58/activation-manifest.json

# 1. Draft patch: one entry per item, with read-only _context (outlet, name, variant, description, price)
.venv/bin/python scripts/taste_tags.py export --catalog $C --outlet-manifest $M

# 2. Fill entries outlet by outlet (edit data/enrichment/taste-tags.patch.json), validating as you go
.venv/bin/python scripts/taste_tags.py validate --catalog $C --outlet-manifest $M
.venv/bin/python scripts/taste_tags.py stats

# 3. STOP. Present a per-outlet review table to the user (see "Review hand-back").
# 4. After the user approves: set review.status="approved", review.reviewer="<user>", then
.venv/bin/python scripts/taste_tags.py apply --catalog $C --outlet-manifest $M \
    --version 0.3.0-tagged \
    --out var/catalog-import/kl-selangor-real-pilot-58-tagged/catalog.validated.json
# 5. Index preflight only. Do NOT run --rebuild without separate approval: it switches the active index.
.venv/bin/python scripts/build_catalog_vector_index.py \
    --catalog var/catalog-import/kl-selangor-real-pilot-58-tagged/catalog.validated.json \
    --outlet-manifest $M --dry-run
```

`apply` refuses to overwrite files, to reuse the input version, or to run with any validation
error. It writes a `taste-tags.apply-receipt.json` with SHA-256s of the input, the patch and the
output.

## What to fill in for each entry

| Field | Rule |
|---|---|
| `meal_role` | One of `main`, `set`, `side`, `dessert`, `beverage`, `add_on`, `unknown` (definitions below) |
| `attributes` | Only tags from the ontology table below. If you are unsure, leave a dimension out. A missing tag is neutral; a wrong tag misleads the ranking. |
| `evidence` | Where it came from: `name`, `variant`, `description`, `menu_code`. Required whenever anything changes. Never "guess", "photo" or "model". |
| `confidence` | `high`: the menu text says it plainly. `medium`: standard meaning of a well-known dish. `low`: unsure. A low-confidence entry can never be approved. |
| `proposed_new_tags` | Concepts that don't fit the ontology (e.g. `burger`, `sushi`, `curry`, `dim_sum`, `kebab`). They are recorded for a later ontology decision and **never applied**. |
| `notes` | Short. Add `sharing` for platters, whole fish, sets for 2 or more, per-100 g or pot items. Add `price_per_weight` for per-100 g prices. |
| `review.status` | Always `needs_review` from you. Only the user approves. |

### `meal_role`

| Role | Use for | Examples from this catalog |
|---|---|---|
| `main` | One person's meal | Chicken Dum Briyani, Tonkotsu Ramen, 大哥叉燒飯, Nasi Lemak Ayam Goreng, Pad Kra Pao Chicken |
| `set` | A combo or set meal for one | Dosirak lunch box, Gohan Set, Set 1 Asam Laksa Udang, SET AYAM GORENG MCK |
| `side` | Small plates, snacks, plain rice or egg | Fried Egg, 饭, Edamame, French fries, Spring Rolls, Telur Mata Kerbau, YTF pieces |
| `dessert` | Sweet course | Baklava, Kakigori, Cendol Durian, 豆花, Creme Caramel, ABC Special |
| `beverage` | **Every** drink, including alcohol, coffee, tea, juice, shakes and bubble tea | Teh O Lemon Ais, Latte, Carlsberg, Milo Dinosaur, Snow Cheese Milk Tea |
| `add_on` | Toppings, syrups, extra shots, sauces | Pearl, Brown Sugar Syrup, Extra Shot, Ajitsuke Egg, 另加加央 |
| `unknown` | The text can't tell you (e.g. `S [Regular]`, `TF01C *Chicken`) | Leave as unknown and add a note |

Sharing plates (Hakka Large, Green View Medium/Large, whole fish, 三人海鲜套餐) are still
`main` or `set`, but each one needs the `sharing` note. Those notes are used later for
`serves_min` and `serves_max`; this pass doesn't set servings.

### Ontology (`dining-tags-v1`)

| Dimension | Allowed tags | Max per item |
|---|---|---|
| dish | soup, noodles, rice, pasta, pizza, salad, sandwich, grill, noodle_soup | several only if the dish truly is both |
| flavour | rich, light, smoky, sweet, sour, savoury, spicy | several |
| spice | none, mild, medium, hot | **1** |
| portion | light, regular, hearty | **1** (`light` is also a flavour) |

Local mapping guide. Map only when someone asking for that tag would accept the dish:

- `noodle_soup`: laksa (asam, curry, nyonya), pho / 河粉 soup, ramen in broth, tom yam noodles, curry mee, 云吞面 (soup), fish ball noodle soup, nabeyaki udon, hor fun ayam sup
- `noodles`: mee goreng, kuey teow / bihun goreng, Hokkien mee (福建面), 干捞 (dry) noodles, tsukemen, yaki udon, char kuey teow, Pad Kra Pao Mama Noodles
- `rice`: nasi lemak, nasi kunyit, nasi goreng, biryani / briyani, fried rice (炒饭), 叉燒飯, donburi / don, claypot rice, porridge (粥) → `rice` + `soup`
- `soup`: sup ayam, tom yam (no noodles), broths, 汤
- `grill`: bakar, satay, yakitori, kebab, tandoori, steak, BBQ, 烤
- `sandwich`: sandwiches, panini, toast sandwiches, croissant sandwiches. **Not** burgers: put `burger` in `proposed_new_tags`.
- `spice`: only if the text says so (pedas, spicy, 麻辣, 香辣, sambal, "hot", Geki Kara). Otherwise leave spice out. **Never default to `mild`.**
- `portion`: only from explicit words (Small → light, Large/Big/Platter → hearty). Otherwise leave it out.
- Chinese-, Malay- and Japanese-only names: read and translate them. Cite `name` as evidence.

### Hard limits

- **Never** add requirement claims as tags (vegetarian, vegan, halal, no pork, gluten-free, nut-free …). The validator rejects them. Requirements need their own verified evidence.
- Don't touch the quarantined item (`ditaliane-ioi-alfredo-funghi-fettuccine-3-pcs-beef-meatballs`) or any outlet outside the 58. The validator rejects both.
- Don't edit `dining/`, `tests/`, the source catalog or `var/vector/`.
- Don't commit unless the user asks.

## Review hand-back (step 3)

Give the user:

1. The `stats` output: role counts, tagged-item coverage, top proposed new tags.
2. A table for each outlet: item, role, tags, confidence, notes. Put drink-heavy outlets first: Moomin Bubbles, Soulja, Dè Pine, Red Kettle, the Mamak Cafe branches, 103 Coffee.
3. Every `low` and `unknown` entry, and every `sharing` / `price_per_weight` note.
4. The ontology additions you would propose, with counts.

## Done when

- `validate` reports **0 errors**.
- Every drink is `beverage`, and `meal_role=unknown` is at most 2% of items, each with a note.
- Every `main`/`set` with a recognisable dish family carries its dish tag.
- The user has approved, `apply` has produced the `0.3.0-tagged` catalog and receipt, and the index `--dry-run` passes.
- Re-index and activation are **not** part of this task. Ask the user first.

## Follow-ups for the main session (not this one)

- Recommender: exclude `add_on` as a meal as well. Today it skips `drink/beverage/dessert/side` only (`dining/recommendation/engine.py`, `meal_role` check).
- Personal recommendations: apply the same `meal_role` filter and the per-diner checks (`dining/recommendation/personal.py`).
- Decide on ontology extensions from `proposed_new_tags` (needs `dining/recommendation/ranking.py` DIMENSIONS/ALIASES, a version bump and contract tests).
- Re-run both golden suites after re-indexing.
