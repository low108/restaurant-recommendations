# Production golden tests — 58 verified outlets with embedded menus

**Status:** DRAFT for review. **Not executed.** Awaiting approval before a runner is written or any case is run.  
**Date:** 7 October 2026  
**Companion files:** `docs/GOLDEN_PRODUCTION_58_TESTS.xlsx` (same 100 cases, one row each), `docs/GOLDEN_RECOMMENDATION_TESTS.md` (100 fixture cases).

## 1. What this suite tests

These cases use the **real activated catalog**, not the fictional fixture:

| Item | Value |
|---|---|
| Catalog | `kl-selangor-real-pilot` **0.4.0-translated** (production since 7 Oct 2026) — `var/catalog-import/kl-selangor-real-pilot-58-translated/catalog.validated.json`. Written against 0.2.0-reviewed; tags (0.3.0) and translations (0.4.0) were added later. |
| Scope | 58 outlets in `activation-manifest.json` → `allowed_outlet_ids`; 1,073 reviewed items + 1 quarantined |
| Vector index | Chroma `var/vector/catalog`, `paraphrase-multilingual-MiniLM-L12-v2`, 1,073 vectors (`active_index.json`) |
| Excluded | 70 outlets (`unresolved_location_hours`) must never appear |

Every expected outlet and dish below was read from that catalog: names, prices, published weekly hours, closed days, halal evidence and coordinates.

## 2. Facts about the real data that shape the expectations

- **Taste tags are almost empty.** Only 2 of 1,074 items have curated attributes. On real data, the PRD craving score C is therefore neutral (.5) for dishes, so *finding the right restaurant depends on the menu embeddings*. These cases use **human relevance labels** (PRD §15.3): which outlets in range actually serve what was asked for.
- **Cuisine tags** exist on only 19 outlets (e.g. Cili Kampung = Malay). Cuisine matches there are deterministic.
- **`meal_role` is `unknown` on every item.** Many menus are mostly drinks (Moomin Bubbles, Soulja, Dè Pine, Red Kettle, Mamak Lake City). The expectation is that a drink, side or dessert is never assigned as someone's meal.
- **Prices:** only 16 items (all Restoran Green View) have all-in payable totals. Others have a listed dine-in price. Some have none (Onsemiro, four Green View sang har dishes). Some are per 100 g (PRIME) or sharing sizes.
- **Halal:** no outlet has a current certificate. Cili Kampung, PRIME and Big Singh have restaurant claims only. Onsemiro is `not_halal`.
- **Diet / allergy / access:** only 3 items carry a vegetarian claim (all D'italiane; one is quarantined). Ingredients are incomplete everywhere. Accessibility is unknown for all 58 outlets.
- **Hours:** all 58 have published weekly hours, but none has dated-exception coverage. 12 outlets close on one or two days a week (e.g. Super Ramen Wed, Mi House Mon, Hide Sun–Mon). Several have split shifts (Green View and Hakka close mid-afternoon).

## 3. Defaults and meeting points

**Default diner:** no allergy, no halal requirement, requirements reviewed and confirmed, **firm cap RM60**. The listed dine-in price is treated as the per-person price, which is the current production policy recorded in `VERIFIED_58_VECTOR_TEST_REPORT.md` §7. Unknown charges are shown as a trade-off.  
**Default meal:** Thursday 12:30 MYT, 60 minutes. Each case names its own meeting point, radius and time.

| Key | Meeting point | Coordinates |
|---|---|---|
| PJ | Petaling Jaya SS2 / Sec 19 | 3.1200, 101.6250 |
| OKR | Old Klang Road / Jalan Klang Lama | 3.0690, 101.6930 |
| KLCC | KLCC (Suria) | 3.1579, 101.7123 |
| CHERAS | Cheras C180 | 3.0364, 101.7660 |
| SS15 | Subang Jaya SS15 | 3.0775, 101.5883 |
| PUCHONG | Puchong | 3.0400, 101.6180 |
| KEPONG | Kepong / Segambut | 3.2050, 101.6600 |
| SHAHALAM | Shah Alam Tadisma | 3.0879, 101.5456 |
| PUDU | Pudu / Cochrane | 3.1000, 101.7400 |
| BRICK | Brickfields / KL Sentral | 3.1330, 101.6880 |
| PENANG | George Town, Penang (unsupported) | 5.4141, 100.3288 |

## 4. Pass rules

| Notation | Meaning |
|---|---|
| **★1. X — must be #1** | `options[0]` must be outlet X |
| **Shortlist = {…}** / **Must include** | Each listed outlet must appear in `options` (any order unless numbered) |
| **Options ⊆ {…}** | No outlet outside the set may appear |
| **Personal** | That diner's `my_personal_recommendations[0]` must be the named outlet, with a dish from the listed set. "/" means any of these dishes is correct. |
| **Must NOT** | A hard fail if it happens anywhere in that case's group or personal results |
| Status | Exact `result.status` |

Every case also checks these invariants: at most 3 options with no duplicate outlet; each option ∈ the 58 allowed IDs; the quarantined item never appears; no assigned dish exceeds that diner's firm cap or has an unknown price; no assigned meal is an obvious drink; personal outlets ⊆ the group shortlist; no private field (craving text, budget, requirement) appears in the shared result.

Ranking-quality cases (sections A, B, H and selected E) are also scored with **Precision@3, Recall@3, NDCG@3, MRR and Top-1** by `scripts/evaluate_ranking.py`, comparing nearest-eligible, average-fit, the PRD blend and content similarity on the same eligible pool (PRD §15.3). Results are in `docs/CONTENT_SIMILARITY.md`. Before 7 Oct 2026 this line claimed metrics that were not computed.


## A. Dish relevance (menu embeddings)

### P-001 · Ramen next to three unrelated outlets
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 3 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: ramen · RM60 cap; P2: Craving: ramen · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Super Ramen (0.15 km) — must be #1
- **Personal best fit:** P1: Super Ramen – a ramen (Terry's Chicky Ramen RM15.90 / Whatever Ramen / Curry Katsu Ramen / Beef Ramen); P2: Super Ramen – a ramen (Terry's Chicky Ramen RM15.90 / Whatever Ramen / Curry Katsu Ramen / Beef Ramen)
- **Must NOT:** #1 = 103 Coffee Workshop or 桂林人 (same building, no ramen)
- **Why:** Super Ramen is the only ramen place within 3 km. 103 Coffee and 桂林人 share its exact coordinates, so if relevance is ignored, distance or ID could put them first.

### P-002 · Ramen beats closer non-ramen outlets
- **Setup:** Kepong / Segambut (3.2050, 101.6600), radius 4 km, Thu 19:00 MYT, 60 min
- **Diners:** P1: Craving: ramen · RM60 cap; P2: Craving: ramen · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Menya Yamato (3.5 km) — must be #1
- **Personal best fit:** P1: Menya Yamato – Tonkotsu Ramen RM18 / Spicy Miso Ramen RM19 / Tsukemen RM22; P2: Menya Yamato – Tonkotsu Ramen RM18 / Spicy Miso Ramen RM19 / Tsukemen RM22
- **Must NOT:** #1 = Mamak Lake City, Bean Jr, Man Kee (closer, no ramen)
- **Why:** Three outlets are closer but none serves ramen. Relevance must outrank proximity when a real match exists in range.

### P-003 · Chinese-language craving: 拉面
- **Setup:** Kepong / Segambut (3.2050, 101.6600), radius 4 km, Thu 19:00 MYT, 60 min
- **Diners:** P1: Craving: 拉面 · RM60 cap; P2: Craving: 拉面 · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Menya Yamato — must be #1
- **Personal best fit:** P1: Menya Yamato – Tonkotsu Ramen / Spicy Miso Ramen; P2: Menya Yamato – Tonkotsu Ramen / Spicy Miso Ramen
- **Must NOT:** #1 = Bean Jr (Chinese menu, desserts)
- **Why:** The embedding model is multilingual. A Chinese query must find English-named ramen dishes.

### P-004 · Nasi lemak at KL Sentral
- **Setup:** Brickfields / KL Sentral (3.1330, 101.6880), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: nasi lemak · RM60 cap; P2: Craving: nasi lemak · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. 151 Hugh Low Kopitiam NU Sentral (0.12 km)
- **Personal best fit:** P1: 151 Hugh Low – Lemongrass Fried Chicken Nasi Lemak RM12.90 / Curry Vegetable Nasi Lemak / Sambal Prawn Nasi Lemak; P2: 151 Hugh Low – Lemongrass Fried Chicken Nasi Lemak RM12.90 / Curry Vegetable Nasi Lemak / Sambal Prawn Nasi Lemak
- **Must NOT:** #1 = Nasi Kandar Mamak Cafe Brickfields (verified menu is drinks + roti), PRIME
- **Why:** Only 151 Hugh Low lists nasi lemak.

### P-005 · Nasi lemak in Shah Alam
- **Setup:** Shah Alam Tadisma (3.0879, 101.5456), radius 6 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: nasi lemak · RM60 cap; P2: Craving: nasi lemak · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Lambogrill Coffee House Tadisma (0.0 km)
- **Personal best fit:** P1: Lambogrill – Nasi Lemak Ayam Goreng RM12; P2: Lambogrill – Nasi Lemak Ayam Goreng RM12
- **Must NOT:** —
- **Why:** Lambogrill (tagged Western) has Nasi Lemak Ayam Goreng. The cuisine tag must not hide a matching dish.

### P-006 · Laksa: relevance over distance
- **Setup:** Subang Jaya SS15 (3.0775, 101.5883), radius 3 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: laksa · RM60 cap; P2: Craving: laksa · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Jom Laksa (1.87 km)
- **Personal best fit:** P1: Jom Laksa – Asam Laksa Udang RM13.90 / Kari Laksa Udang Besar RM13.90 / Nyonya Laksa Ayam Pedas Cutlet RM13.90; P2: Jom Laksa – Asam Laksa Udang RM13.90 / Kari Laksa Udang Besar RM13.90 / Nyonya Laksa Ayam Pedas Cutlet RM13.90
- **Must NOT:** #1 = SOI55 Thai Kitchen or Big Singh Chapati (closer, no laksa)
- **Why:** SOI55 and Big Singh are much closer but don't serve laksa.

### P-007 · Curry mee
- **Setup:** Pudu / Cochrane (3.1000, 101.7400), radius 5 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: curry mee · RM60 cap; P2: Craving: curry mee · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. JIA LI MIAN NOODLE HOUSE (3.88 km)
- **Personal best fit:** P1: Jia Li Mian – Penang Curry Mee RM7 / KL Curry Mee RM7; P2: Jia Li Mian – Penang Curry Mee RM7 / KL Curry Mee RM7
- **Must NOT:** #1 = Yakitori HAKI / Dè Pine Cafe (closer)
- **Why:** Jia Li Mian is a curry-mee specialist. Closer outlets have no curry noodles.

### P-008 · Malay-language craving: mi kari
- **Setup:** Pudu / Cochrane (3.1000, 101.7400), radius 5 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: mi kari · RM60 cap; P2: Craving: mi kari · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. JIA LI MIAN NOODLE HOUSE
- **Personal best fit:** P1: Jia Li Mian – Penang Curry Mee / KL Curry Mee; P2: Jia Li Mian – Penang Curry Mee / KL Curry Mee
- **Must NOT:** —
- **Why:** Bahasa Melayu query must retrieve the same dish family as P-007.

### P-009 · Malay cuisine at KLCC
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Malay · RM60 cap; P2: Cuisine today: Malay · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Cili Kampung Suria KLCC (0.06 km)
- **Personal best fit:** P1: Cili Kampung – Ayam Kunyit RM15 / Sup Ayam RM15 / Masak Lomak Signature (Chicken) RM40; P2: Cili Kampung – Ayam Kunyit RM15 / Sup Ayam RM15 / Masak Lomak Signature (Chicken) RM40
- **Must NOT:** Personal dish = Pisang Goreng Gula Hangus Aiskrim (dessert)
- **Why:** Cili Kampung is the only Malay-tagged outlet. This is a structured cuisine match, so it is deterministic.

### P-010 · Turkish cuisine — meal not a drink
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Turkish · RM60 cap; P2: Cuisine today: Turkish · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Grand Hisar Stonor KLCC (1.08 km)
- **Personal best fit:** P1: Grand Hisar – a main: Cheese/Spinach Gözleme RM18, Adana/Iskender Kebab RM44, Turkish Scrambled Eggs RM23; P2: Grand Hisar – a main: Cheese/Spinach Gözleme RM18, Adana/Iskender Kebab RM44, Turkish Scrambled Eggs RM23
- **Must NOT:** Dish = Turkish Coffee, Ayran, Baklava, Kunafa, Rice Pudding
- **Why:** All Grand Hisar items share the Turkish tag. A cheapest-item tie-break must not pick a drink or dessert as the meal.

### P-011 · Hakka cuisine
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Hakka · RM60 cap; P2: Cuisine today: Hakka · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Hakka (0.8 km)
- **Personal best fit:** P1: Hakka – Szechwan Spicy & Sour Seafood Soup RM20 / Crab Meat & Egg Soup (Small) RM20 / Hakka Special Yam Abacus RM45; P2: Hakka – Szechwan Spicy & Sour Seafood Soup RM20 / Crab Meat & Egg Soup (Small) RM20 / Hakka Special Yam Abacus RM45
- **Must NOT:** Dish = a Large sharing plate (Gui Hua Chi Large RM100, Sliced Abalone RM220+)
- **Why:** Only Hakka is Hakka-tagged. Sharing-size plates are not per-person meals.

### P-012 · Burger in PJ
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: burger · RM60 cap; P2: Craving: burger · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. myBurgerLab SeaPark (1.07 km)
- **Personal best fit:** P1: myBurgerLab – Mr Brightside RM22.90 / Beautiful Mess RM26.90 / The Chuck Norris RM29.50 / Salted Egg Yolk Burger RM29.90; P2: myBurgerLab – Mr Brightside RM22.90 / Beautiful Mess RM26.90 / The Chuck Norris RM29.50 / Salted Egg Yolk Burger RM29.90
- **Must NOT:** #1 = Restoran Green View or AROI Mak Mak (closer, no burger)
- **Why:** myBurgerLab is the only burger outlet within 2 km.

### P-013 · Thai food from an untagged outlet
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Thai · RM60 cap; P2: Cuisine today: Thai · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. AROI Mak Mak PJ (0.53 km)
- **Personal best fit:** P1: AROI – Green Curry Chicken with Rice RM16 / Thai Style Fried Chicken RM18 / Stir Fried Thai Basil Pork RM25; P2: AROI – Green Curry Chicken with Rice RM16 / Thai Style Fried Chicken RM18 / Stir Fried Thai Basil Pork RM25
- **Must NOT:** #1 = Restoran Green View (Chinese seafood; only 'Thai Style' side plates)
- **Why:** AROI has no cuisine tag, but its whole menu is Thai. Only the menu embeddings can find it.

### P-014 · Seafood in PJ
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: seafood · RM60 cap; P2: Craving: seafood · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Restoran Green View (0.47 km); 2. AROI Mak Mak PJ acceptable
- **Personal best fit:** P1: Green View – Seafood Tofu Broth (per pax) RM18 / House Special Honey Squid (Small) RM30; P2: Green View – Seafood Tofu Broth (per pax) RM18 / House Special Honey Squid (Small) RM30
- **Must NOT:** —
- **Why:** Green View is tagged Chinese + Seafood and has a priced, all-in seafood menu.

### P-015 · Waffles / brunch
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: waffle brunch · RM60 cap; P2: Craving: waffle brunch · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Any shortlist; no dessert or drink as the assigned meal
- **Personal best fit:** P1: A non-dessert, non-drink main from the shortlist; P2: A non-dessert, non-drink main from the shortlist
- **Must NOT:** Classic Waffle, French toast or any coffee assigned as the meal
- **Why:** Red Kettle's waffles and French toast are tagged dessert and the rest of its menu is coffee, so it can't provide the meal. (Updated 7 Oct 2026 for decision 2: desserts are never a meal.)

### P-016 · Beef pho — Chinese-only menu names
- **Setup:** Cheras C180 (3.0364, 101.7660), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: beef pho · RM60 cap; P2: Craving: beef pho · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Viet Pho Cafe (0.42 km)
- **Personal best fit:** P1: Viet Pho Cafe – 招牌石鍋牛肉河粉 (signature stone-pot beef pho) RM31; P2: Viet Pho Cafe – 招牌石鍋牛肉河粉 (signature stone-pot beef pho) RM31
- **Must NOT:** #1 = BAR.BER or BLACK TOWER COFFEE (0.0 km, no pho)
- **Why:** The dish name is only in Chinese. A cross-language match must outrank the two closer outlets.

### P-017 · Thai in Cheras
- **Setup:** Cheras C180 (3.0364, 101.7660), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Thai · RM60 cap; P2: Cuisine today: Thai · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Khunthai Village Restaurant (1.8 km)
- **Personal best fit:** P1: Khunthai – Mixed Seafood RM25 / Steamed Lala in Lemon Sauce RM18 / Kerabu Mango RM12; P2: Khunthai – Mixed Seafood RM25 / Steamed Lala in Lemon Sauce RM18 / Kerabu Mango RM12
- **Must NOT:** #1 = Viet Pho Cafe, BAR.BER, BLACK TOWER
- **Why:** The only Thai outlet in range has no cuisine tag. It must be found from its menu.

### P-018 · Pizza — two equally relevant outlets
- **Setup:** Cheras C180 (3.0364, 101.7660), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: pizza · RM60 cap; P2: Craving: pizza · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** 1–2. BAR.BER Bar & Kitchen and BLACK TOWER COFFEE (both 0.0 km, either order)
- **Personal best fit:** P1: BLACK TOWER – Hawaiian Pizza RM19.90, or BAR.BER – 素食比萨 RM21.80 / 午餐肉披萨 RM26; P2: BLACK TOWER – Hawaiian Pizza RM19.90, or BAR.BER – 素食比萨 RM21.80 / 午餐肉披萨 RM26
- **Must NOT:** #1 = Viet Pho Cafe
- **Why:** Both co-located outlets serve pizza. Either may be first.

### P-019 · Sushi in Puchong
- **Setup:** Puchong (3.0400, 101.6180), radius 3 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: sushi · RM60 cap; P2: Craving: sushi · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Nakamura Bashi Puchong (0.63 km)
- **Personal best fit:** P1: Nakamura Bashi – 鳗鱼寿司 (eel sushi) RM9.80 / 鲑鱼手卷 RM7.80 / 中华海蜇寿司 RM7.80; P2: Nakamura Bashi – 鳗鱼寿司 (eel sushi) RM9.80 / 鲑鱼手卷 RM7.80 / 中华海蜇寿司 RM7.80
- **Must NOT:** #1 = Hee Lai Ton (0.61 km, Chinese banquet)
- **Why:** Hee Lai Ton is 20 m closer but has no sushi.

### P-020 · Hokkien mee
- **Setup:** Puchong (3.0400, 101.6180), radius 3 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: Hokkien mee · RM60 cap; P2: Craving: Hokkien mee · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Xin Hao Tat Restaurant (2.05 km)
- **Personal best fit:** P1: Xin Hao Tat – 福建面 (Hokkien mee) RM8.50; P2: Xin Hao Tat – 福建面 (Hokkien mee) RM8.50
- **Must NOT:** #1 = Hee Lai Ton / Nakamura Bashi / De Forest
- **Why:** Only Xin Hao Tat lists 福建面. The English query must match the Chinese name.

### P-021 · Fish ball noodles — two relevant
- **Setup:** Puchong (3.0400, 101.6180), radius 3 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: fish ball noodles · RM60 cap; P2: Craving: fish ball noodles · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** 1. De Forest Cafe (1.5 km)  2. Xin Hao Tat (2.05 km)
- **Personal best fit:** P1: De Forest – 福州鱼丸粉 RM10.90 (or Xin Hao Tat – 鱼片米粉 RM12); P2: De Forest – 福州鱼丸粉 RM10.90 (or Xin Hao Tat – 鱼片米粉 RM12)
- **Must NOT:** #1 = Hee Lai Ton / Nakamura Bashi
- **Why:** Both serve fish noodles. They tie on fit, so the closer one (De Forest) leads.

### P-022 · Char siu rice
- **Setup:** Kepong / Segambut (3.2050, 101.6600), radius 3 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: char siu rice · RM60 cap; P2: Craving: char siu rice · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. 文记冰室 Man Kee Cafe (2.23 km)
- **Personal best fit:** P1: Man Kee – 大哥叉燒飯 配煎双蛋 RM12.80; P2: Man Kee – 大哥叉燒飯 配煎双蛋 RM12.80
- **Must NOT:** #1 = Mamak Lake City / Bean Jr (closer)
- **Why:** Only Man Kee serves 叉燒飯.

### P-023 · Soy dessert / tau fu fa
- **Setup:** Kepong / Segambut (3.2050, 101.6600), radius 3 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: tau fu fa · RM60 cap; P2: Craving: tau fu fa · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Any shortlist that gives a real meal
- **Personal best fit:** P1: Best main in the pool (豆花 is a dessert); P2: Best main in the pool (豆花 is a dessert)
- **Must NOT:** 豆花 or any dessert/drink assigned as the meal
- **Why:** Bean Jr only sells desserts, drinks and toppings, so the craving can't be served as a meal. (Updated 7 Oct 2026 for decision 2: desserts are never a meal.)

### P-024 · Yong tau foo
- **Setup:** Pudu / Cochrane (3.1000, 101.7400), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: yong tau foo · RM60 cap; P2: Craving: yong tau foo · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Cheras Homey Yong Tau Foo (0.49 km)
- **Personal best fit:** P1: Cheras Homey – YTF 01 Signature Yong Tau Foo RM6 / Classic Yong Tau Foo RM1.80; P2: Cheras Homey – YTF 01 Signature Yong Tau Foo RM6 / Classic Yong Tau Foo RM1.80
- **Must NOT:** #1 = Yakitori HAKI (0.11 km) / Dè Pine Cafe (0.27 km)
- **Why:** The relevant outlet is the 3rd closest.

### P-025 · Yakitori
- **Setup:** Pudu / Cochrane (3.1000, 101.7400), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: yakitori · RM60 cap; P2: Craving: yakitori · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Yakitori HAKI (0.11 km)
- **Personal best fit:** P1: Yakitori HAKI – G6. Yakitori Don RM22; P2: Yakitori HAKI – G6. Yakitori Don RM22
- **Must NOT:** Dish = Carlsberg / Sake / Coke
- **Why:** Relevant and closest. The dish must be food, not one of its many drinks.

### P-026 · Arabic food
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: Arabic food · RM60 cap; P2: Craving: Arabic food · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. AL HARAMAIN RESTAURANT (0.75 km)
- **Personal best fit:** P1: Al Haramain – Arayis RM12 / Madhghout RM18 / Aqdah Chicken RM11; P2: Al Haramain – Arayis RM12 / Madhghout RM18 / Aqdah Chicken RM11
- **Must NOT:** #1 = 103 Coffee / Super Ramen / 桂林人 (0.15 km)
- **Why:** The only Middle-Eastern menu is the 8th closest outlet.

### P-027 · Tom yam — two relevant
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: tom yam · RM60 cap; P2: Craving: tom yam · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** 1–2. WARUNG MAKCIK KIAH (0.16 km) and Thai Chala (0.22 km)
- **Personal best fit:** P1: Warung Makcik Kiah – Tomyam Campur RM12.90, or Thai Chala – Tomyam Seafood Soup RM18.90; P2: Warung Makcik Kiah – Tomyam Campur RM12.90, or Thai Chala – Tomyam Seafood Soup RM18.90
- **Must NOT:** #1 = 103 Coffee / 桂林人
- **Why:** Two outlets in range list tom yam.

### P-028 · Hot pot
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: hot pot steamboat · RM60 cap; P2: Craving: hot pot steamboat · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. There's A Hot Pot Restaurant (1.36 km)
- **Personal best fit:** P1: There's A Hot Pot – single-person item e.g. 鲜菇鸡肉面 RM8.90 / 牛肉片 RM9.90; P2: There's A Hot Pot – single-person item e.g. 鲜菇鸡肉面 RM8.90 / 牛肉片 RM9.90
- **Must NOT:** Dish = 三人海鲜套餐 (3-person seafood set RM108)
- **Why:** Relevant outlet. A 3-person set must not be priced as one person's meal.

### P-029 · Chicken chop
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: chicken chop · RM60 cap; P2: Craving: chicken chop · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. GaGa Western Corner (0.19 km)
- **Personal best fit:** P1: GaGa – C2 黑椒煎雞扒 RM16 / C12 芝士煎雞扒 RM18; P2: GaGa – C2 黑椒煎雞扒 RM16 / C12 芝士煎雞扒 RM18
- **Must NOT:** #1 = 103 Coffee / Super Ramen / 桂林人
- **Why:** Chinese-named chicken chops must match an English craving.

### P-030 · Biryani
- **Setup:** Subang Jaya SS15 (3.0775, 101.5883), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: biryani · RM60 cap; P2: Craving: biryani · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Big Singh Chapati SS15 (0.56 km)
- **Personal best fit:** P1: Big Singh – Chicken Dum Briyani RM18.90; P2: Big Singh – Chicken Dum Briyani RM18.90
- **Must NOT:** #1 = SOI55 Thai Kitchen (0.01 km)
- **Why:** Relevance beats the closer Thai outlet.


## B. Mixed group cravings

### P-031 · Ramen / nasi goreng / dessert
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: ramen · RM60 cap; P2: Craving: nasi goreng · RM60 cap; P3: Craving: kakigori dessert · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Shortlist = {Super Ramen, WARUNG MAKCIK KIAH, Shibuya Dessert} (any order)
- **Personal best fit:** P1: Super Ramen – a ramen; P2: Warung Makcik Kiah – 602. Nasi Goreng Cina RM9.90; P3: Shibuya Dessert – a sandwich (TC5 Charcoal Chicken Sandwich RM10.90 …); kakigori is a dessert
- **Must NOT:** 103 Coffee / 桂林人 taking a slot
- **Why:** Each person's craving is served by a different outlet in range. A good shortlist covers all three, so everyone's personal best fit is in the pool.

### P-032 · Malay / Turkish / Chinese at KLCC
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 1.2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Malay · RM60 cap; P2: Cuisine today: Turkish · RM60 cap; P3: Cuisine today: Chinese · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** 1. Cili Kampung  2. Hakka  3. Grand Hisar (distance order; all three must appear)
- **Personal best fit:** P1: Cili Kampung – Ayam Kunyit / Sup Ayam; P2: Grand Hisar – Gözleme or Kebab; P3: Hakka – Spicy & Sour Seafood Soup / Crab Meat & Egg Soup
- **Must NOT:** Hide KL / Onsemiro in options
- **Why:** Each cuisine has exactly one tagged outlet in range. The group shortlist should include one for each person.

### P-033 · Burger + Thai
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: burger · RM60 cap; P2: Cuisine today: Thai · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Must include myBurgerLab SeaPark and AROI Mak Mak PJ
- **Personal best fit:** P1: myBurgerLab – Mr Brightside; P2: AROI – Green Curry Chicken with Rice
- **Must NOT:** —
- **Why:** The two cravings map to two different nearby outlets. Both must reach the shortlist.

### P-034 · Four friends incl. a bubble-tea craver
- **Setup:** Subang Jaya SS15 (3.0775, 101.5883), radius 2.5 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: laksa · RM60 cap; P2: Craving: biryani · RM60 cap; P3: Craving: pad kra pao · RM60 cap; P4: Craving: bubble tea · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Shortlist = {SOI55 Thai Kitchen, Big Singh Chapati, Jom Laksa}
- **Personal best fit:** P1: Jom Laksa – Asam Laksa Udang; P2: Big Singh – Chicken Dum Briyani; P3: SOI55 – Pad Kra Pao Chicken RM16; P4: Best food match in pool (e.g. SOI55) — not a drink-only outlet
- **Must NOT:** Moomin Bubbles (drinks/toppings only) in options
- **Why:** Moomin Bubbles serves only drinks, so it cannot give the other three a meal. P4's drink craving is soft.

### P-035 · Pho + pizza in Cheras
- **Setup:** Cheras C180 (3.0364, 101.7660), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: beef pho · RM60 cap; P2: Craving: pizza · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Must include Viet Pho Cafe and one of BAR.BER / BLACK TOWER
- **Personal best fit:** P1: Viet Pho – 招牌石鍋牛肉河粉; P2: BLACK TOWER – Hawaiian Pizza (or BAR.BER pizza)
- **Must NOT:** —
- **Why:** Both cravings are available within 1 km.

### P-036 · Sushi / Hokkien mee / chicken chop
- **Setup:** Puchong (3.0400, 101.6180), radius 2.5 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: sushi · RM60 cap; P2: Craving: Hokkien mee · RM60 cap; P3: Craving: chicken chop · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Shortlist = {Nakamura Bashi, De Forest Cafe, Xin Hao Tat}
- **Personal best fit:** P1: Nakamura Bashi – eel sushi; P2: Xin Hao Tat – 福建面; P3: De Forest – W0005 香煎鸡扒 RM14.90 / R0014 鸡扒配白饭
- **Must NOT:** Hee Lai Ton (banquet set menus) ahead of a relevant outlet
- **Why:** One relevant outlet per person.

### P-037 · Kepong: char siu / naan / dessert
- **Setup:** Kepong / Segambut (3.2050, 101.6600), radius 3 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: char siu rice · RM60 cap; P2: Craving: cheese naan · RM60 cap; P3: Craving: tau fu fa · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Shortlist = {Bean Jr, Nasi Kandar Mamak Cafe Taman Impian, Man Kee Cafe}
- **Personal best fit:** P1: Man Kee – 大哥叉燒飯; P2: Mamak Taman Impian – NT.545 Naan Cheese Double RM9; P3: Best main in the pool (豆花 is a dessert)
- **Must NOT:** Nasi Kandar Mamak Cafe Lake City (verified menu is drinks only)
- **Why:** Lake City is closest, but its verified menu has only drinks. Taman Impian (same brand) has the naan.

### P-038 · Nasi lemak + roti
- **Setup:** Brickfields / KL Sentral (3.1330, 101.6880), radius 0.5 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: nasi lemak · RM60 cap; P2: Craving: roti canai · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Must include 151 Hugh Low Kopitiam and Nasi Kandar Mamak Cafe Brickfields
- **Personal best fit:** P1: 151 Hugh Low – Lemongrass Fried Chicken Nasi Lemak; P2: Mamak Brickfields – RT.222 Roti Telur Bawang RM3.50
- **Must NOT:** PRIME as #1
- **Why:** Both cravings are within 0.5 km.

### P-039 · Four cuisines; two can't clear budget
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 1.2 km, Thu 19:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Malay · RM60 cap; P2: Cuisine today: Korean · RM60 cap; P3: Cuisine today: Turkish · RM60 cap; P4: Cuisine today: Japanese · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Shortlist ⊆ {Cili Kampung, Hakka, Grand Hisar}; Onsemiro under Needs confirmation
- **Personal best fit:** P1: Cili Kampung – Ayam Kunyit; P2: Best in pool (no verified-price Korean dish); P3: Grand Hisar – Gözleme/Kebab; P4: Best in pool (Hide tasting menu RM698 > cap)
- **Must NOT:** Hide KL or Onsemiro in options
- **Why:** Onsemiro's dishes have no price except a RM113 set. Hide's tasting menus are RM398–698. Neither clears a RM60 cap.

### P-040 · Two people with no preference
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: anything · RM60 cap; P2: Craving: anything · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Any 3 open outlets in range, each with a real main dish
- **Personal best fit:** P1: Any main from the #1 outlet; P2: Any main from the #1 outlet
- **Must NOT:** A drink, side or dessert assigned as the meal
- **Why:** With neutral taste, any eligible outlet is fine. The assigned item must still be a meal.

### P-041 · Vegetarian + seafood lover
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 5 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · **hard:** Vegetarian · RM60 cap; P2: Craving: seafood · RM60 cap
- **Expected status:** `shortlisted (1 option)`
- **Group ideal outlets:** ★1. D'italiane IOI Mall Damansara (only option)
- **Personal best fit:** P1: D'italiane – Plant-based Meatball RM25 / Double Plant-based Meat-lover Sandwich RM38; P2: D'italiane – D'italiane Greek Prawn Salad RM25 / Sicilian Seafood Pizza RM42
- **Must NOT:** Green View / AROI in options
- **Why:** Only D'italiane has dishes with a verified vegetarian claim. Green View suits P2 but cannot verify a vegetarian dish for P1.

### P-042 · Tom yam + non-spicy chicken chop
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: tom yam · RM60 cap; P2: Craving: chicken chop, not spicy · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Must include GaGa Western Corner and one of Warung Makcik Kiah / Thai Chala
- **Personal best fit:** P1: Warung Makcik Kiah – Tomyam Campur (or Thai Chala – Tomyam Seafood Soup); P2: GaGa – 黑椒煎雞扒 / 芝士煎雞扒
- **Must NOT:** —
- **Why:** Both cravings are available within 0.25 km.


## C. Hard requirements on real data

### P-043 · Certified halal — no certified outlet exists
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · **hard:** Certified halal only · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `needs_verification`
- **Group ideal outlets:** None
- **Personal best fit:** P1: None; P2: None
- **Must NOT:** Any option; Cili Kampung shown as halal
- **Why:** None of the 58 outlets has a current certificate record. Cili Kampung, PRIME and Big Singh only have restaurant claims, which never count as certified.

### P-044 · Certified halal in SS15
- **Setup:** Subang Jaya SS15 (3.0775, 101.5883), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · **hard:** Certified halal only · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `needs_verification`
- **Group ideal outlets:** None
- **Personal best fit:** P1: None; P2: None
- **Must NOT:** Big Singh in options
- **Why:** Big Singh's halal evidence is a restaurant claim, not a certificate.

### P-045 · Halal: 'show info, I'll decide'
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · **hard:** Halal: show information, I'll decide · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `needs_verification`
- **Group ideal outlets:** None until P1 accepts per meal
- **Personal best fit:** P1: None; P2: None
- **Must NOT:** Silently treating claims as certified
- **Why:** PRD §6.1: this policy needs explicit per-meal acceptance of any unresolved requirement.

### P-046 · Halal policy unanswered
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · **hard:** Halal: unknown · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `needs_verification`
- **Group ideal outlets:** None
- **Personal best fit:** P1: None; P2: None
- **Must NOT:** —
- **Why:** An unanswered hard requirement blocks generation.

### P-047 · Vegetarian in PJ
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 5 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · **hard:** Vegetarian · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `shortlisted (1 option)`
- **Group ideal outlets:** ★1. D'italiane IOI Mall Damansara (only option)
- **Personal best fit:** P1: D'italiane – Plant-based Meatball / Double Plant-based Meat-lover Sandwich; P2: D'italiane – any main
- **Must NOT:** AROI in options because a dish is named 'Vegetarian Thai Fried Rice'; the quarantined Alfredo Funghi Fettuccine
- **Why:** A dish name is not verified evidence. Only items with a reviewed vegetarian claim count. AROI belongs under Needs confirmation.

### P-048 · Vegetarian where no verified dish exists
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 3 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · **hard:** Vegetarian · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `needs_verification`
- **Group ideal outlets:** None
- **Personal best fit:** P1: None; P2: None
- **Must NOT:** Al Haramain (Falafel) or Thai Chala as an ordinary option
- **Why:** No outlet near Old Klang Road has a reviewed vegetarian claim.

### P-049 · Vegan
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 5 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · **hard:** Vegan · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `needs_verification`
- **Group ideal outlets:** None
- **Personal best fit:** P1: None; P2: None
- **Must NOT:** D'italiane plant-based items treated as vegan
- **Why:** D'italiane's plant-based items claim vegetarian, not vegan. Parmesan is listed in the description.

### P-050 · Declared peanut allergy
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · **hard:** Allergy: peanut (declared) · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `needs_verification`
- **Group ideal outlets:** None
- **Personal best fit:** P1: None; P2: None
- **Must NOT:** 'peanut' in the shared result
- **Why:** No kitchen confirmations exist, and every item's ingredients and allergen data are unknown.

### P-051 · Must avoid pork (ingredient rule)
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · **hard:** Must avoid: pork · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `needs_verification`
- **Group ideal outlets:** None
- **Personal best fit:** P1: None; P2: None
- **Must NOT:** Thai Chala (pork dishes) or GaGa (猪扒) as ordinary options
- **Why:** Every item has ingredients_complete=false, so 'no pork' cannot be verified anywhere.

### P-052 · Shellfish allergy with one kitchen confirmation
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 2 km, Thu 12:30 MYT, 60 min; Green View kitchen confirmed the Roasted Sha Tin Chicken is prepared without shellfish (valid until after the meal)
- **Diners:** P1: No preference · **hard:** Allergy: shellfish (declared) · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `shortlisted (1 option)`
- **Group ideal outlets:** ★1. Restoran Green View (only ordinary option); AROI, myBurgerLab, Red Kettle still offered under Needs confirmation
- **Personal best fit:** P1: Green View – Roasted Sha Tin Chicken RM13 (the confirmed order); P2: Green View – any priced dish
- **Must NOT:** AROI / myBurgerLab / Red Kettle as ordinary options, or silently dropped instead of listed under Needs confirmation
- **Why:** Decision 7 Oct 2026: a current, specific kitchen confirmation makes Green View eligible, as long as the other outlets are still offered. They stay visible under Needs confirmation so the group has alternatives. This is not a safety guarantee.

### P-053 · Step-free entrance required
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · **hard:** Needs step-free entrance · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `needs_verification`
- **Group ideal outlets:** None
- **Personal best fit:** P1: None; P2: None
- **Must NOT:** —
- **Why:** All 58 outlets have unknown accessibility evidence.

### P-054 · Quiet seating as a soft preference
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Prefers quiet seating (preferred, not required) · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Normal shortlist (Cili Kampung first by distance)
- **Personal best fit:** P1: Cili Kampung – any main; P2: Cili Kampung – any main
- **Must NOT:** Status needs_verification
- **Why:** A preferred feature is soft. Unknown evidence lowers coverage but never blocks.

### P-055 · Requirements not reviewed
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · RM60 cap; P2: No preference · **hard:** Requirements NOT reviewed · RM60 cap
- **Expected status:** `needs_verification`
- **Group ideal outlets:** None
- **Personal best fit:** P1: None; P2: None
- **Must NOT:** —
- **Why:** Everyone must confirm requirements first.

### P-056 · Only one diner
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · RM60 cap
- **Expected status:** `needs_input`
- **Group ideal outlets:** None
- **Personal best fit:** P1: None
- **Must NOT:** —
- **Why:** A one-person session is not a group recommendation.

### P-057 · Unsupported area (Penang)
- **Setup:** George Town, Penang (unsupported) (5.4141, 100.3288), radius 5 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `no_options (unsupported area)`
- **Group ideal outlets:** None
- **Personal best fit:** P1: None; P2: None
- **Must NOT:** Any KL/Selangor outlet
- **Why:** No verified outlet is within 5 km. The app must say the area is unsupported and must not stretch the radius.

### P-058 · Very small radius
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 0.5 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `shortlisted (1 option)`
- **Group ideal outlets:** ★1. Cili Kampung Suria KLCC (only outlet ≤ 0.5 km)
- **Personal best fit:** P1: Cili Kampung – Ayam Kunyit / Sup Ayam; P2: Cili Kampung – Ayam Kunyit / Sup Ayam
- **Must NOT:** Hide KL (0.7 km), Hakka (0.8 km)
- **Why:** The radius is a hard filter.

### P-059 · Quarantined dish never used
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 5 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: fettuccine alfredo · RM60 cap; P2: Craving: fettuccine alfredo · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. D'italiane IOI Mall Damansara
- **Personal best fit:** P1: D'italiane – Smoked & Peppered Salmon Alfredo [Penne] RM50 / Grilled Chicken & Basil Pesto Pasta [Fettuccine] RM42; P2: D'italiane – Smoked & Peppered Salmon Alfredo [Penne] RM50 / Grilled Chicken & Basil Pesto Pasta [Fettuccine] RM42
- **Must NOT:** Alfredo Funghi Fettuccine [3 PCS BEEF MEATBALLS] anywhere
- **Why:** That item is quarantined: its vegetarian claim conflicts with its beef variant.

### P-060 · The 70 unverified outlets never appear
- **Setup:** Brickfields / KL Sentral (3.1330, 101.6880), radius 6 km, Thu 12:30 MYT, 60 min; repeat with KLCC r6 'dim sum'
- **Diners:** P1: Craving: nasi kandar · RM60 cap; P2: Craving: nasi kandar · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Only verified outlets, e.g. Nasi Kandar Mamak Cafe Brickfields / Danau Kota (Nasi Briyani Kosong)
- **Personal best fit:** P1: A verified mamak outlet's food item; P2: A verified mamak outlet's food item
- **Must NOT:** RESTORAN NASI KANDAR ARIFF, HAMEEDS MAJU, WS Dim Sum or any other excluded outlet
- **Why:** Strong menu matches exist among the 70 excluded outlets. They must never leak into options or personal results.


## D. Budget & price evidence

### P-061 · Tight cap RM8
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · RM8 cap; P2: No preference · RM8 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. AL HARAMAIN RESTAURANT
- **Personal best fit:** P1: Al Haramain – Falafel Sandwich RM6 / Egg Shakshooka RM7 / Pasta RM8 / Homous RM7; P2: Al Haramain – Falafel Sandwich RM6 / Egg Shakshooka RM7 / Pasta RM8 / Homous RM7
- **Must NOT:** Meal = Coke, 100号, 炸薯條 (fries), Telur Mata Kerbau (fried egg) or any drink
- **Why:** Al Haramain has several real meals at or below RM8. Drinks and sides at other outlets don't count as a meal.

### P-062 · Cap RM5 at KL Sentral
- **Setup:** Brickfields / KL Sentral (3.1330, 101.6880), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · RM5 cap; P2: No preference · RM5 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** 1. Nasi Kandar Mamak Cafe Brickfields  2. 151 Hugh Low (acceptable)
- **Personal best fit:** P1: Mamak Brickfields – Roti Telur Bawang RM3.50 / Roti Bawang RM2.40; P2: Mamak Brickfields – Roti Telur Bawang RM3.50 / Roti Bawang RM2.40
- **Must NOT:** PRIME in options; a drink (Teh O Lemon Ais RM3.50) as the meal
- **Why:** Roti is a legitimate cheap meal. PRIME has nothing near RM5.

### P-063 · Steak on a RM60 cap
- **Setup:** Brickfields / KL Sentral (3.1330, 101.6880), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: steak · RM60 cap; P2: Craving: steak · RM60 cap
- **Expected status:** `shortlisted (without PRIME)`
- **Group ideal outlets:** PRIME must NOT be shortlisted
- **Personal best fit:** P1: Best in pool (151 Hugh Low – Grilled Salmon with Chips & Salad RM27.90 acceptable); P2: Best in pool (151 Hugh Low – Grilled Salmon with Chips & Salad RM27.90 acceptable)
- **Must NOT:** PRIME with Potatoes [Pont Neuf Fries] RM25 or Greens RM38 as the 'steak' meal
- **Why:** Every PRIME steak costs ≥ RM230. Sides under the cap do not make a steak dinner affordable.

### P-064 · Steak on a RM400 cap
- **Setup:** Brickfields / KL Sentral (3.1330, 101.6880), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: steak · RM400 cap; P2: Craving: steak · RM400 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. PRIME Kuala Lumpur (Le Méridien)
- **Personal best fit:** P1: PRIME – Devesa Chilled Beef Tenderloin (220g) RM260 / Pure Black Angus Tenderloin (220g) RM328; P2: PRIME – Devesa Chilled Beef Tenderloin (220g) RM260 / Pure Black Angus Tenderloin (220g) RM328
- **Must NOT:** Dish = an item priced 'per 100g' (A5 Ribeye, Sher Wagyu) treated as a whole meal
- **Why:** Per-100g prices are not the price of a meal. Fixed-weight steaks are.

### P-065 · Dish with no listed price
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: sang har noodle · RM60 cap; P2: Craving: sang har noodle · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Restoran Green View — with a priced dish
- **Personal best fit:** P1: Green View – a priced dish (e.g. Seafood Tofu Broth RM18); sang har noodle shown only as 'price needs confirmation'; P2: Green View – a priced dish (e.g. Seafood Tofu Broth RM18); sang har noodle shown only as 'price needs confirmation'
- **Must NOT:** Green View Signature Sang Har Noodle (no price) assigned as the budget-cleared dish
- **Why:** An unknown price cannot clear a firm cap (PRD §9.2).

### P-066 · Korean place with almost no prices
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 1.2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Korean · RM60 cap; P2: Cuisine today: Korean · RM60 cap
- **Expected status:** `shortlisted (without Onsemiro) / Onsemiro in Needs confirmation`
- **Group ideal outlets:** Onsemiro under Needs confirmation; shortlist from other outlets
- **Personal best fit:** P1: Best in pool (Cili Kampung / Hakka / Grand Hisar); P2: Best in pool (Cili Kampung / Hakka / Grand Hisar)
- **Must NOT:** Onsemiro with Kimchi Stew (no price) as an ordinary option
- **Why:** Onsemiro's only priced dish is RM113, over the cap. The rest have unknown prices.

### P-067 · Japanese tasting menu over cap
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 1 km, Thu 19:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Japanese · RM100 cap; P2: Cuisine today: Japanese · RM100 cap
- **Expected status:** `shortlisted (without Hide)`
- **Group ideal outlets:** Hide KL must NOT appear
- **Personal best fit:** P1: Best in pool; P2: Best in pool
- **Must NOT:** Hide KL in options
- **Why:** Hide's only dishes are tasting menus at RM398–698.

### P-068 · Sushi omakase vs cap
- **Setup:** Kepong / Segambut (3.2050, 101.6600), radius 4 km, Thu 19:00 MYT, 60 min; (b) cap RM150
- **Diners:** P1: Craving: sushi · RM60 cap; P2: Craving: sushi · RM60 cap
- **Expected status:** `(a) Tsukiji not shortlisted  (b) shortlisted`
- **Group ideal outlets:** (a) Tsukiji must NOT be #1 on RM60;  (b) ★1. Tsukiji Sushi Plaza Arkadia
- **Personal best fit:** P1: (b) Tsukiji – Tekka Don RM88 / Chirashi Don RM128; P2: (b) Tsukiji – Tekka Don RM88 / Chirashi Don RM128
- **Must NOT:** (a) Tsukiji with Cawan Mushi RM18 / Suimono RM30 as the sushi meal
- **Why:** Under RM60, Tsukiji has only sides and desserts.

### P-069 · Comfortable target RM15 (soft) + cap RM60
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: noodles · Comfortable RM15, cap RM60; P2: Craving: noodles · Comfortable RM15, cap RM60
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Noodle outlets in range; cheaper noodle dishes preferred
- **Personal best fit:** P1: Warung Makcik Kiah – Mee Goreng Biasa RM9.90 / GaGa – 清湯伊麵 RM8.50; P2: Warung Makcik Kiah – Mee Goreng Biasa RM9.90 / GaGa – 清湯伊麵 RM8.50
- **Must NOT:** Personal pick above RM15 when a ≤RM15 noodle dish is in the pool
- **Why:** The soft target lowers the score of pricier dishes without excluding them.

### P-070 · Per-person budget isn't split from sharing dishes
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Chinese · RM40 cap; P2: Cuisine today: Chinese · RM40 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Hakka
- **Personal best fit:** P1: Hakka – Szechwan Spicy & Sour Seafood Soup RM20 / Crab Meat Tempura (Medium) RM30; P2: Hakka – Szechwan Spicy & Sour Seafood Soup RM20 / Crab Meat Tempura (Medium) RM30
- **Must NOT:** Dish = Hakka Clay Pot Rice Wine Soup (Large) RM260 'divided by 2'
- **Why:** Large or sharing items are never divided into per-person prices.


## E. Opening hours

### P-071 · Saturday breakfast 08:00
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 3 km, Sat 08:00 MYT, 60 min
- **Diners:** P1: No preference · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Options ⊆ {103 Coffee Workshop, Chok Kar Chong}
- **Personal best fit:** P1: 103 Coffee – B.L.T RM24.90 / Yuzu pizza, or Chok Kar Chong – 干贝粥 RM23; P2: 103 Coffee – B.L.T RM24.90 / Yuzu pizza, or Chok Kar Chong – 干贝粥 RM23
- **Must NOT:** Super Ramen, Shibuya, GaGa, Thai Chala, Warung, 桂林人, Al Haramain (not yet open)
- **Why:** Only 103 Coffee (07:30) and Chok Kar Chong (07:00) are open at 08:00.

### P-072 · Supper at 01:00
- **Setup:** Brickfields / KL Sentral (3.1330, 101.6880), radius 1 km, Fri 01:00 MYT, 60 min
- **Diners:** P1: No preference · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `shortlisted (1 option)`
- **Group ideal outlets:** ★1. Nasi Kandar Mamak Cafe Brickfields (24h)
- **Personal best fit:** P1: Mamak Brickfields – Roti Telur Bawang / Roti Bawang; P2: Mamak Brickfields – Roti Telur Bawang / Roti Bawang
- **Must NOT:** 151 Hugh Low (closes 21:00), PRIME (closes 22:00)
- **Why:** Only the 24-hour mamak is open.

### P-073 · Late night 23:30 in Cheras
- **Setup:** Cheras C180 (3.0364, 101.7660), radius 1 km, Thu 23:30 MYT, 60 min
- **Diners:** P1: No preference · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `shortlisted (1 option)`
- **Group ideal outlets:** ★1. BAR.BER Bar & Kitchen (open to 03:00)
- **Personal best fit:** P1: BAR.BER – 扬州炒饭 RM10.80 / 海鲜意粉 RM23.30; P2: BAR.BER – 扬州炒饭 RM10.80 / 海鲜意粉 RM23.30
- **Must NOT:** BLACK TOWER (closes 23:00), Viet Pho (closes 22:30)
- **Why:** Recurring weekly hours are published for every outlet and must be applied.

### P-074 · Hide KL closed on Monday
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 1.2 km, Mon 19:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Japanese · RM800 cap; P2: Cuisine today: Japanese · RM800 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Hide KL must NOT appear
- **Personal best fit:** P1: Best open option; P2: Best open option
- **Must NOT:** Hide KL in options
- **Why:** Hide opens Tue–Sat only.

### P-075 · Afternoon gap 15:30
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 2 km, Thu 15:30 MYT, 60 min
- **Diners:** P1: Craving: seafood · RM60 cap; P2: Craving: seafood · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Options ⊆ {myBurgerLab, Red Kettle}
- **Personal best fit:** P1: myBurgerLab – a burger (Red Kettle waffles are desserts); P2: myBurgerLab – a burger (Red Kettle waffles are desserts)
- **Must NOT:** Restoran Green View (closed 15:00–17:30), AROI (closed 14:30–17:30)
- **Why:** The most relevant outlets are on a split-shift break. The relevance of a closed outlet must not override its hours.

### P-076 · Super Ramen closed Wednesday
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 3 km, Wed 12:30 MYT, 60 min
- **Diners:** P1: Craving: ramen · RM60 cap; P2: Craving: ramen · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Super Ramen must NOT appear; Zakuro Japanese (Mentaiko Yaki Udon / Nabe Yaki) acceptable #1
- **Personal best fit:** P1: Zakuro – CS12 Mentaiko Yaki Udon & 4 Seasons Salmon Sushi RM38; P2: Zakuro – CS12 Mentaiko Yaki Udon & 4 Seasons Salmon Sushi RM38
- **Must NOT:** Super Ramen in options
- **Why:** On its closed day, the best match must drop out. The nearest Japanese noodle option is a sensible substitute.

### P-077 · Mi House closed Monday
- **Setup:** Cheras C180 (3.0364, 101.7660), radius 4 km, Mon 12:30 MYT, 60 min
- **Diners:** P1: Craving: wonton noodles · RM60 cap; P2: Craving: wonton noodles · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Mi House must NOT appear
- **Personal best fit:** P1: Best open noodle option (Fei Po Ban Mee – 鱼丸干捞 / 咖喱面); P2: Best open noodle option (Fei Po Ban Mee – 鱼丸干捞 / 咖喱面)
- **Must NOT:** Mi House in options
- **Why:** Mi House (云吞面) would be the best match but is closed on Mondays.

### P-078 · Yong tau foo on Tuesday
- **Setup:** Pudu / Cochrane (3.1000, 101.7400), radius 1 km, Tue 12:30 MYT, 60 min
- **Diners:** P1: Craving: yong tau foo · RM60 cap; P2: Craving: yong tau foo · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Cheras Homey Yong Tau Foo must NOT appear
- **Personal best fit:** P1: Best open option (Yakitori HAKI); P2: Best open option (Yakitori HAKI)
- **Must NOT:** Cheras Homey YTF or Dè Pine Cafe (both closed Tue)
- **Why:** Both outlets are closed on Tuesday.

### P-079 · 03:00 supper — 24h mamak with food
- **Setup:** Kepong / Segambut (3.2050, 101.6600), radius 2.5 km, Sat 03:00 MYT, 60 min
- **Diners:** P1: No preference · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Nasi Kandar Mamak Cafe Taman Impian
- **Personal best fit:** P1: Mamak Taman Impian – NT.545 Naan Cheese Double RM9 / NK.539 Sup Ayam RM8; P2: Mamak Taman Impian – NT.545 Naan Cheese Double RM9 / NK.539 Sup Ayam RM8
- **Must NOT:** Mamak Lake City as the meal outlet (its verified menu is drinks only)
- **Why:** Both mamak branches are open 24h. Only Taman Impian's verified menu has food. Same-brand dedupe must not keep the drinks-only branch and drop the food one.

### P-080 · Meal would run past closing
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 2 km, Thu 21:30 MYT, 60 min
- **Diners:** P1: No preference · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `shortlisted (1 option)`
- **Group ideal outlets:** ★1. Red Kettle The Starling Mall (open to 23:00)
- **Personal best fit:** P1: Red Kettle – Fried Nian Gao (its only non-dessert food); P2: Red Kettle – Fried Nian Gao (its only non-dessert food)
- **Must NOT:** Green View / myBurgerLab (close 22:00), AROI (closes 21:00–21:30)
- **Why:** A 60-minute meal from 21:30 needs the outlet open until 22:30.


## F. Personal best fit

### P-081 · Two cravings, two personal winners
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: ramen · RM60 cap; P2: Craving: tom yam · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Must include Super Ramen and one of Warung Makcik Kiah / Thai Chala
- **Personal best fit:** P1: Super Ramen – a ramen; P2: Warung Makcik Kiah – Tomyam Campur, or Thai Chala – Tomyam Seafood Soup
- **Must NOT:** P1's personal #1 not at Super Ramen
- **Why:** Each person's private best fit follows their own craving.

### P-082 · English cravings → Chinese dish names, same outlet
- **Setup:** Cheras C180 (3.0364, 101.7660), radius 4 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: wonton noodles · RM60 cap; P2: Craving: pineapple fried rice · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Mi House (Kajang)
- **Personal best fit:** P1: Mi House – N07 云吞面 RM6.90; P2: Mi House – R05 菠萝炒饭 RM8.90
- **Must NOT:** Same dish for both
- **Why:** Mi House serves both. The personal dish must match each person's own craving.

### P-083 · Two rice dishes at Man Kee
- **Setup:** Kepong / Segambut (3.2050, 101.6600), radius 3 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: char siu rice · RM60 cap; P2: Craving: salt-baked chicken rice · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Man Kee Cafe
- **Personal best fit:** P1: Man Kee – 302 大哥叉燒飯 配煎双蛋 RM12.80; P2: Man Kee – 307 東江鹽焗手撕雞飯 RM12.80
- **Must NOT:** —
- **Why:** Same outlet, different personal dish.

### P-084 · Vegetarian personal dish
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 5 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: No preference · **hard:** Vegetarian · RM60 cap; P2: Craving: pasta · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. D'italiane
- **Personal best fit:** P1: D'italiane – Plant-based Meatball / Double Plant-based Meat-lover Sandwich; P2: D'italiane – a pasta (Bolognaise Chicken Angel Hair RM33 …)
- **Must NOT:** P1 personal = Italian Chicken Meatballs or any non-claimed item
- **Why:** P1's personal dish must carry the verified vegetarian claim.

### P-085 · Firm cap applies to personal dish
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 1.2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Malay · RM20 cap; P2: Cuisine today: Malay · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Cili Kampung
- **Personal best fit:** P1: Cili Kampung – Ayam Kunyit RM15 / Sup Ayam RM15; P2: Cili Kampung – any Malay main
- **Must NOT:** P1 personal = Masak Lomak (≥ RM40) or Ayam Bakar RM50
- **Why:** A personal pick must still clear that person's own firm cap.

### P-086 · Coffee craving still gets a food item
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: coffee · RM60 cap; P2: Craving: burger · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Must include myBurgerLab; Red Kettle acceptable
- **Personal best fit:** P1: A non-drink, non-dessert main (e.g. myBurgerLab burger); P2: myBurgerLab – a burger
- **Must NOT:** P1 personal dish = Latte / Mocha / Espresso
- **Why:** The personal recommendation is a meal. A drink-only pick is not a 'best fit' for lunch.

### P-087 · Spicy dish over cap
- **Setup:** Shah Alam Tadisma (3.0879, 101.5456), radius 6 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: sambal pedas · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Lambogrill
- **Personal best fit:** P1: Lambogrill – Shanghai Lamb Chili RM45 / Mee Goreng Mamak RM19; P2: Lambogrill – any main
- **Must NOT:** P1 personal = Ikan Siakap Sambal Pedas (RM65 > RM60 cap)
- **Why:** The best text match is over P1's cap, so the next-best affordable dish is chosen.

### P-088 · Quarantined dish never personal
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 5 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: alfredo · RM60 cap; P2: Craving: alfredo · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. D'italiane
- **Personal best fit:** P1: D'italiane – Smoked & Peppered Salmon Alfredo [Penne] RM50; P2: D'italiane – Smoked & Peppered Salmon Alfredo [Penne] RM50
- **Must NOT:** Alfredo Funghi Fettuccine [3 PCS BEEF MEATBALLS]
- **Why:** The closest name match is quarantined.

### P-089 · Unpriced dish never personal
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: sang har noodle · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Restoran Green View
- **Personal best fit:** P1: Green View – a priced dish; the unpriced Sang Har Noodle only flagged; P2: Any
- **Must NOT:** Green View Signature Sang Har Noodle / Braised Sang Har E-Fu Noodle as P1's budget-cleared pick
- **Why:** These items have no price, so they cannot be confirmed against a firm cap.

### P-090 · Same outlet, different pad kra pao
- **Setup:** Subang Jaya SS15 (3.0775, 101.5883), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: pad kra pao beef · RM60 cap; P2: Craving: pad kra pao seafood mama noodles · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. SOI55 Thai Kitchen
- **Personal best fit:** P1: SOI55 – Pad Kra Pao Beef RM20; P2: SOI55 – Pad Kra Pao Seafood Mama Noodles RM18
- **Must NOT:** Same dish for both
- **Why:** Menu-level matching must separate similar variants.

### P-091 · Steak personal uses fixed-weight steak
- **Setup:** Brickfields / KL Sentral (3.1330, 101.6880), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: wagyu steak · RM400 cap; P2: No preference · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. PRIME
- **Personal best fit:** P1: PRIME – Devesa Chilled Beef Tenderloin (220g) RM260 / Pure Black Angus (220g) RM328; P2: Any
- **Must NOT:** P1 personal = 'per 100g' item treated as a full meal
- **Why:** A per-100g price is not a full-meal price.

### P-092 · Neutral diner gets a sensible main
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 1.2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: anything · RM60 cap; P2: Craving: anything · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Cili Kampung first (closest)
- **Personal best fit:** P1: Cili Kampung – a main (Ayam Kunyit / Sup Ayam); P2: Cili Kampung – a main (Ayam Kunyit / Sup Ayam)
- **Must NOT:** Personal = a dessert (Pisang Goreng …) or a drink
- **Why:** With no preference, the personal pick is still a real meal.

### P-093 · Personal picks only from the group shortlist
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: ramen · RM60 cap; P2: Craving: nasi goreng · RM60 cap; P3: Craving: kakigori dessert · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** As P-031
- **Personal best fit:** P1: Super Ramen – ramen; P2: Warung – Nasi Goreng Cina; P3: Shibuya – Kakigori
- **Must NOT:** A personal outlet that isn't in the shortlist
- **Why:** MAKAN-212: personal results come only from the group-eligible pool.

### P-094 · Personal results are repeatable
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min; run twice and with check-in order reversed
- **Diners:** P1: Craving: ramen · RM60 cap; P2: Craving: tom yam · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Identical across runs
- **Personal best fit:** P1: Identical outlet, dish, rank and reasons each run; P2: Identical outlet, dish, rank and reasons each run
- **Must NOT:** Any difference between runs
- **Why:** Deterministic scoring and retrieval are required.

### P-095 · Personal picks are private
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min; view as P1 and as P2
- **Diners:** P1: Craving: ramen · RM60 cap; P2: Craving: tom yam · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** As P-081
- **Personal best fit:** P1: Visible only to P1; P2: Visible only to P2
- **Must NOT:** Another diner's picks, scores or reasons; craving text in the shared result
- **Why:** Each person sees only their own picks, and the shared result has no private fields.


## G. Retrieval integrity & privacy

### P-096 · Semantic retrieval is active and labelled
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 3 km, Thu 12:30 MYT, 60 min (as P-001)
- **Diners:** P1: Craving: ramen · RM60 cap; P2: Craving: ramen · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** As P-001
- **Personal best fit:** P1: As P-001; P2: As P-001
- **Must NOT:** retrieval_status ≠ semantic
- **Why:** result.retrieval_status = 'semantic', embedding_model = paraphrase-multilingual-MiniLM-L12-v2, and the index covers 1,073 items / 58 outlets.

### P-097 · Index unavailable → honest structured fallback
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 3 km, Thu 12:30 MYT, 60 min; embedding index removed
- **Diners:** P1: Craving: ramen · RM60 cap; P2: Craving: ramen · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Any open outlets in range; retrieval_status = structured_fallback
- **Personal best fit:** P1: Any eligible main; P2: Any eligible main
- **Must NOT:** Claiming semantic retrieval; any hard-gate violation
- **Why:** Without embeddings, relevance isn't guaranteed. The result must say so, and every hard check must still hold.

### P-098 · An unrelated dish doesn't make an outlet relevant
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: pizza · RM60 cap; P2: Craving: pizza · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. 103 Coffee Workshop (Yuzu pizza)
- **Personal best fit:** P1: 103 Coffee – Yuzu pizza RM19.90; P2: 103 Coffee – Yuzu pizza RM19.90
- **Must NOT:** #1 = 桂林人 or Super Ramen (same location, no pizza)
- **Why:** Of the three co-located outlets, only 103 Coffee has a pizza.

### P-099 · Private craving text stays private
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: something for my diabetes, low sugar · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** Any
- **Personal best fit:** P1: Any; P2: Any
- **Must NOT:** Craving text, health words or embeddings in shared result, receipts, traces or logs
- **Why:** Retrieval receipts are structural only (PRD REQ-18, REQ-20).

### P-100 · Scope check across the whole suite
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min; check the union of every result in P-001 – P-099
- **Diners:** P1: No preference · RM60 cap; P2: No preference · RM60 cap
- **Expected status:** `—`
- **Group ideal outlets:** Every option and personal outlet ∈ the 58 allowed outlet IDs
- **Personal best fit:** P1: —; P2: —
- **Must NOT:** Any excluded outlet or the quarantined item, in any result
- **Why:** This is a whole-suite guard on the activation manifest scope.


## H. Majority preference

### P-101 · Two of three want Malay, one Turkish
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 1.2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Malay · RM60 cap; P2: Cuisine today: Malay · RM60 cap; P3: Cuisine today: Turkish · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Cili Kampung Suria KLCC; Grand Hisar must also appear
- **Personal best fit:** P1: Cili Kampung – Ayam Kunyit RM15; P2: Cili Kampung – Ayam Kunyit RM15; P3: Grand Hisar – Adana Kebab RM44
- **Must NOT:** Grand Hisar or Hakka as #1
- **Why:** Cili Kampung scores .5076 (two diners at .725, one at .365); Grand Hisar .4464. The majority's cuisine wins, and the Turkish diner's restaurant still makes the list.

### P-102 · Two Malay, one Japanese
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 1.2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Malay · RM60 cap; P2: Cuisine today: Malay · RM60 cap; P3: Cuisine today: Japanese · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Cili Kampung Suria KLCC
- **Personal best fit:** P1: Cili Kampung – Ayam Kunyit; P2: Cili Kampung – Ayam Kunyit; P3: Best in pool (no Japanese meal under RM60)
- **Must NOT:** Any other outlet as #1
- **Why:** No Japanese meal is affordable nearby, so the majority's Malay choice leads.

### P-103 · Two Malay, one Chinese
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 1.2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Malay · RM60 cap; P2: Cuisine today: Malay · RM60 cap; P3: Cuisine today: Chinese · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Cili Kampung Suria KLCC; Hakka must also appear
- **Personal best fit:** P1: Cili Kampung – Ayam Kunyit; P2: Cili Kampung – Ayam Kunyit; P3: Hakka – a Chinese main
- **Must NOT:** Hakka as #1
- **Why:** Hakka serves the Chinese diner and appears in the list, but doesn't outrank the majority's choice.

### P-104 · Two Malay, one Korean
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 1.2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Malay · RM60 cap; P2: Cuisine today: Malay · RM60 cap; P3: Cuisine today: Korean · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Cili Kampung Suria KLCC; Onsemiro under Needs confirmation
- **Personal best fit:** P1: Cili Kampung – Ayam Kunyit; P2: Cili Kampung – Ayam Kunyit; P3: Best in pool
- **Must NOT:** Onsemiro as an ordinary option
- **Why:** The Korean restaurant's dishes have no price, so it stays under Needs confirmation; the majority's choice leads.

### P-105 · Two want ramen, one nasi goreng
- **Setup:** Old Klang Road / Jalan Klang Lama (3.0690, 101.6930), radius 1 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Craving: ramen · RM60 cap; P2: Craving: ramen · RM60 cap; P3: Craving: nasi goreng · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. Super Ramen; Warung Makcik Kiah must also appear
- **Personal best fit:** P1: Super Ramen – a ramen; P2: Super Ramen – a ramen; P3: Warung Makcik Kiah – Nasi Goreng Cina
- **Must NOT:** Warung Makcik Kiah or 103 Coffee as #1
- **Why:** Majority by dish rather than cuisine.

### P-106 · Three want Thai, one burger
- **Setup:** Petaling Jaya SS2 / Sec 19 (3.1200, 101.6250), radius 2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Thai · RM60 cap; P2: Cuisine today: Thai · RM60 cap; P3: Cuisine today: Thai · RM60 cap; P4: Craving: burger · RM60 cap
- **Expected status:** `shortlisted`
- **Group ideal outlets:** ★1. AROI Mak Mak PJ; myBurgerLab must also appear
- **Personal best fit:** P1: AROI – a Thai main; P2: AROI – a Thai main; P3: AROI – a Thai main; P4: myBurgerLab – a burger
- **Must NOT:** myBurgerLab as #1
- **Why:** Three of four is a clear majority; the burger fan's restaurant is still offered.

### P-107 · Majority can't override a hard requirement
- **Setup:** KLCC (Suria) (3.1579, 101.7123), radius 1.2 km, Thu 12:30 MYT, 60 min
- **Diners:** P1: Cuisine today: Malay · RM60 cap; P2: Cuisine today: Malay · RM60 cap; P3: Cuisine today: Malay · **hard:** Certified halal only · RM60 cap
- **Expected status:** `needs_verification`
- **Group ideal outlets:** None
- **Personal best fit:** P1: None; P2: None; P3: None
- **Must NOT:** Cili Kampung shown as an ordinary option
- **Why:** Cili Kampung only has a restaurant halal claim, not a certificate. One person's requirement blocks the outlet regardless of the majority.

## 5. Product decisions (confirmed 7 October 2026)

| # | Question | Decision |
|---|---|---|
| 1 | Firm cap vs listed price | **Yes.** The listed dine-in price is treated as the per-person price for a firm cap. Unknown extra charges are shown as a trade-off, not a block. A dish with **no** price still cannot clear a cap. |
| 2 | Drink, side or dessert as someone's meal | **Never.** Applies to group and personal results. Items need a reliable `meal_role` (see `docs/TASTE_TAGS_HANDOFF.md`). |
| 3 | Relevance vs distance | **Relevance wins.** A restaurant that serves the craved dish ranks above a closer one that doesn't. Distance only breaks ties between equally relevant outlets. |
| 4 | Shellfish confirmation (P-052) | **Yes, if there are other options.** Green View becomes eligible through the confirmation, and the other outlets stay visible under Needs confirmation as alternatives. |

## 6. Cases most likely to fail (from reading the code; not run)

| Cases | Why |
|---|---|
| ★ relevance cases (P-001 – P-030, P-098) | Every dish has C = .5, so after retrieval the order is distance then outlet ID. A closer or co-located non-matching outlet can come first. |
| P-071 – P-080 | With no dated-exception coverage, the hours check returns *unknown*. For ordinary diners that is only a trade-off, so closed outlets can still be shortlisted. |
| P-010, P-015, P-025, P-034, P-079, P-086 | `meal_role` is unknown, so drinks are not filtered. The cheapest-item tie-break favours drinks. |
| P-065, P-089 | In the non-synthetic branch, an item with `price=None` skips the budget check. |
| P-063, P-064, P-091 | Per-100g, sharing and set prices are compared to the cap as if they were per-person meals. |
| P-084, P-087 – P-089 | `score_personal_options` does not re-check per-diner diet, budget, quarantine or price evidence. |
| P-031 – P-042 | The shortlist may not cover each person's craving, so some personal best fits fall outside the pool. |
