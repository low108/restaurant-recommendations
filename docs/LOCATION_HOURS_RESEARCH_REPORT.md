# Location and Operating Hours Research Report

Date: 2026-10-06  
Status: Enriched patch (58 outlets), review queue (70 outlets), and source manifest (99 sources) generated under approved operational criteria.  
Target catalog: `/Users/johnathanjohnathan/Documents/restaurant-menu-collection/catalog.real.json`  
Catalog ID: `kl-selangor-real-pilot` (Version `0.1.0-partial`, SHA-256 `767e25e39957aba4ccb46e49ee136551ebbbc89afe0272282908ce307cf27e77`)

---

## 1. Executive Summary & Policy Updates

This research session investigated public evidence for exact decimal coordinates, recurring operating hours, kitchen last-order cutoffs, and dated exception coverage across all **128 outlets** present in the pilot catalog.

Following operator authorization, the research methodology incorporated two approved policy adjustments:
1. **Official social media announcements** (verified Facebook business pages, Instagram business profiles, official linktr.ee/oddle store announcements) are accepted as authoritative weekly operating hours sources.
2. **Building and commercial street-block coordinates** (e.g. mapped commercial shoplot blocks, mall polygons, and multi-tenant commercial centers) are accepted where exact individual unit polygons are unmapped.

### Key Outcomes:
- **58 outlets (45.3%)** are fully resolved and proposed for activation in `data/enrichment/location-hours.patch.json` with verified building/storefront coordinates and complete weekly recurring schedules.
- **70 outlets (54.7%)** are held for operator review in `data/enrichment/location-hours.review.json` due to missing operating schedules, unmapped locations, branch ambiguities, or verified closures/relocations.
- **5 outlets** have explicit kitchen last-order cutoffs documented (Onsemiro, myBurgerLab, BigBowl Thai, Pormtip Thai, Super Ramen).
- **99 new sources** are defined in `data/enrichment/location-hours.sources.json` conforming strictly to `dining.catalog.models.Source`.
- `catalog.real.json` remains completely unmodified (SHA-256 checksum intact).

---

## 2. Checkpoint Progress Table

The 128 outlets were researched and verified across seven bounded checkpoints:

| Checkpoint | Index Range | Total Outlets | Patched Outlets | Reviewed Outlets | Status | Key Highlights |
|---|---|---|---|---|---|---|
| **CP1** | 0 – 19 | 20 | 18 | 2 | Complete | Curated operators: 18 outlets verified (PRIME, Onsemiro, Big Singh, myBurgerLab, Grand Hisar, Hakka, Hide, Lambogrill, Mamak Cafe Taman Impian/Brickfields/Lake City, D'italiane, Green View, Cili Kampung, SOI55 SS15, Red Kettle, Tsukiji Sushi, Bar.Ber). 2 held for review (La Delima address conflict, Mantra Bar hours typo). |
| **CP2** | 20 – 39 | 20 | 12 | 8 | Complete | ScanMenu outlets: 12 verified (Bean Jr, 桂林人, Yakitori Haki, Zakuro, Al Haramain, 151 Hugh Low, BigBowl Thai, Cheras Homey, De Forest Cafe, Dè Pine Cafe, Fei Po Ban Mee, GaGa Western Corner). 8 held for review (relocations, closures, irregular off-days). |
| **CP3** | 40 – 59 | 20 | 5 | 15 | Complete | ScanMenu outlets: 5 verified (Jom Laksa Da Men Mall, Menya Yamato, Mi House, MY記腸粉, PORMTIP THAI RESTAURANT). 15 held for review. |
| **CP4** | 60 – 79 | 20 | 7 | 13 | Complete | ScanMenu outlets: 7 verified (Shibuya Dessert, Soulja Premium Soya, Sushi Zensai, Thai Chala, There's A Hot Pot, Warung Makcik Kiah, Xin Hao Tat). 13 held for review. |
| **CP5** | 80 – 99 | 20 | 6 | 14 | Complete | ScanMenu outlets: 6 verified (文记冰室 Man Kee, Black Tower Coffee, Chok Kar Chong, Jia Li Mian, Jemi Cafe, Moomin Bubbles Sunway Pyramid). 14 held for review. |
| **CP6** | 100 – 119 | 20 | 6 | 14 | Complete | ScanMenu outlets: 6 verified (Super Ramen, Nakamura Bashi The Sphere, D&Y Corner, 103 Coffee, Mamak Cafe Danau Kota, Nakamura Bashi IOI Prima). 14 held for review. |
| **CP7** | 120 – 127 | 8 | 4 | 4 | Complete | ScanMenu outlets: 4 verified (Khunthai Village Cheras Jaya, AROI Mak Mak PJ, Hee Lai Ton Puchong, Viet Pho Cafe Balakong). 4 held for review. |
| **Total** | **0 – 127** | **128** | **58** | **70** | **Complete** | **Every catalog outlet mapped to exactly one destination. 0 referential integrity errors.** |

---

## 3. Summary Statistics

```text
Input catalog outlets:                             128
Exact branch identities matched in patch:           58
Storefront/building coordinates proposed:           58
  - Storefront precision:                           10
  - Building precision:                             48
Approximate coordinates rejected:                   43
Full weekly schedules proposed:                     58
Explicit last-order cutoffs proposed:                5
Dated exception ranges established:                  0

Unresolved outlets held for review:                 70
Breakdown of review reason codes:
  - hours_missing:                                  55
  - coordinates_approximate:                        43
  - possibly_closed:                                 8
  - hours_incomplete:                                6
  - ambiguous_branch:                                5
  - hours_conflict:                                  4
  - possibly_renamed:                                4
  - address_mismatch:                                2

New sources created (location-hours.sources.json):  99
By kind:
  - other (OpenStreetMap, official social/announcements): 90
  - official_website:                                     9
By freshness state:
  - unresolved_freshness (expires_at=null):               99 (requires operator freshness policy)
By display rights:
  - allowed (OpenStreetMap ODbL 1.0):                     52
  - unknown (operator rights review required):            47
By embedding rights:
  - unknown (operator embedding review required):         99
```

---

## 4. Patched Outlets Overview (58 Outlets)

### Checkpoint 1: Curated Operators (18 Outlets)
1. **`ditaliane-ioi-mall-damansara`**: IOI Mall Damansara (`3.1485586, 101.5960663`, building). Hours: Mon–Sun 10:00–22:00.
2. **`green-view-pj`**: Jalan 19/3 Seksyen 19 PJ (`3.1196701, 101.6292207`, building). Hours: Mon–Sun 11:00–15:00 & 17:30–22:00.
3. **`prime-kuala-lumpur`**: Le Méridien KL Sentral (`3.1356280, 101.6864422`, building). Hours: Mon–Fri 12:00–15:00 & 18:00–22:00; Sat–Sun 18:00–22:00.
4. **`cili-kampung-suria-klcc`**: Suria KLCC (`3.1573751, 101.7123797`, building). Hours: Mon–Sun 10:00–22:00.
5. **`onsemiro-intermark`**: Intermark Mall (`3.1613579, 101.7202036`, storefront). Hours: Mon–Sun 10:00–22:00. **Last order: 21:30**.
6. **`soi55-ss15-subang-jaya`**: 62 Jalan SS 15/4c (`3.0774475, 101.5882697`, storefront). Hours: Mon–Sun 12:00–22:00.
7. **`big-singh-chapati-ss15`**: 41 Jalan SS 15/5a (`3.0801438, 101.5925829`, storefront). Hours: Mon–Sun 11:00–23:00.
8. **`red-kettle-starling`**: The Starling Mall (`3.1353398, 101.6227882`, building). Hours: Mon–Sun 11:00–23:00.
9. **`myburgerlab-seapark`**: 14 Jalan 21/22 SeaPark (`3.1108054, 101.6222152`, storefront). Hours: Mon–Sun 11:00–22:00. **Last order: 21:30** ("9.30 PM last call").
10. **`grand-hisar-stonor`**: 10 Stonor KLCC (`3.1532649, 101.7208314`, building). Hours: Mon–Sun 11:00–23:00.
11. **`hakka-raja-chulan`**: 90 Jalan Raja Chulan (`3.1507060, 101.7129500`, storefront). Hours: Mon–Sun 11:30–14:30 & 17:30–22:30.
12. **`tsukiji-sushi-arkadia`**: Plaza Arkadia Desa ParkCity (`3.1862886, 101.6352359`, building). Hours: Mon–Thu 11:00–15:00 & 18:00–22:00; Fri–Sun 11:00–22:00.
13. **`hide-kl-ampang`**: The Ritz-Carlton Residences (`3.1567108, 101.7060822`, storefront). Hours: Tue–Thu 17:30–22:30; Fri–Sat 12:00–14:30 & 17:30–22:30; Mon & Sun closed.
14. **`lambogrill-shah-alam`**: 5 Jalan Snuker 13/28 Shah Alam (`3.0878785, 101.5455660`, building). Hours: Mon–Fri 10:00–23:00; Sat–Sun 08:00–23:00.
15. **`mamak-cafe-brickfields`**: Menara Sentral Vista (`3.1324810, 101.6902046`, storefront). Hours: Mon–Sun 00:00–00:00 `closes_next_day: true` (24/7).
16. **`mamak-cafe-taman-impian`**: 753 Taman Impian Jalan Ipoh (`3.1992732, 101.6783863`, storefront). Hours: Mon–Sun 00:00–00:00 `closes_next_day: true` (24/7).
17. **`mamak-cafe-lake-city`**: Jalan Sibu Lake City (`3.2110882, 101.6697022`, building). Hours: Mon–Sun 00:00–00:00 `closes_next_day: true` (24/7).
18. **`bar-ber-cheras`**: Dataran C180 Cheras (`3.0364302, 101.7660214`, building). Hours: Mon–Sun 17:00–03:00 `closes_next_day: true`.

### Checkpoint 2: ScanMenu Outlets (12 Outlets)
19. **`scan-bean-jr-menu-68e934`** (Bean Jr Kepong): Jalan Rimbunan Raya 1 (`3.2111970, 101.6494978`, building). Hours: Sun–Thu 12:00–23:00; Fri–Sat 12:00–00:00 next day.
20. **`scan-restoran-gui-lin-sri-petaling-menu-a90c9d`** (桂林人 Sri Petaling): Jalan Radin Bagus 1 (`3.0692952, 101.6943470`, building). Hours: Mon–Sun 10:30–22:30.
21. **`scan-yakitori-haki-menu-e067c1`** (Yakitori Haki Cheras): 34G Jalan 5/101C (`3.1010312, 101.7400064`, storefront). Hours: Mon–Sun 12:00–23:00.
22. **`scan-zakuro-japanese-restaurant-menu-dbd35b`** (Zakuro Bukit Jalil): The Link 2 (`3.0522167, 101.6796454`, building). Hours: Mon–Sun 11:30–22:00.
23. **`scan-al-haramain-restaurant-menu-b385ba`** (Al Haramain Sri Petaling): Endah Promenade (`3.0635002, 101.6969504`, building). Hours: Mon–Sun 09:30–01:00 next day.
24. **`scan-151-hugh-low-kopitiam-nu-sentral-menu-c7e400`** (151 Hugh Low NU Sentral): NU Sentral (`3.1332821, 101.6869945`, building). Hours: Mon–Sun 09:00–21:00.
25. **`scan-bigbowl-thai-food-bbq-menu-4cc9f0`** (BigBowl Thai Pandan Indah): Jalan Pandan Indah 4/6B (`3.1315132, 101.7547621`, building). Hours: Mon–Sun 11:30–22:00. **Last order: 21:30**.
26. **`scan-cheras-homey-yong-tau-foo-menu-100d39`** (Cheras Homey YTF): Jalan Jintan Cheras (`3.0985612, 101.7441833`, building). Hours: Wed–Mon 11:00–20:00 (Tue closed).
27. **`scan-de-forest-cafe-menu-f5b778`** (De Forest Cafe Puchong): Jalan Puteri 1/4 (`3.0265670, 101.6166491`, building). Hours: Mon–Sat 10:00–21:00 (Sun closed).
28. **`scan-de-pine-cafe-menu-e0e601`** (Dè Pine Cafe Cheras): Jalan Cengkeh Taman Cheras (`3.1003075, 101.7424107`, building). Hours: Wed–Mon 10:00–18:00 (Tue closed).
29. **`scan-fei-po-ban-mee-menu-e8b249`** (Fei Po Ban Mee Cheras): Jalan Temenggung 11/9 (`3.0522509, 101.7890196`, building). Hours: Mon–Sun 08:00–21:00.
30. **`scan-gaga-western-corner-menu-54b37b`** (GaGa Western Corner Sri Petaling): Jalan Radin Anum 1 (`3.0672594, 101.6929851`, building). Hours: Wed–Mon 11:00–22:30 (Tue closed).

### Checkpoint 3: ScanMenu Outlets (5 Outlets)
31. **`scan-jom-laksa-menu-57b2d4`** (Jom Laksa Da Men Mall): Da Men Mall USJ (`3.0613096, 101.5928853`, building). Hours: Mon–Sun 10:00–22:00.
32. **`scan-menya-yamato-e9-ba-b5-e5-b1-8b-e5-a4-a7--d7cfa9`** (Menya Yamato Manjalara): Jalan 3/62A (`3.1951778, 101.6300246`, building). Hours: Mon–Fri 11:30–15:00 & 17:00–22:00; Sat–Sun 11:00–22:00.
33. **`scan-mi-house-menu-9d5721`** (Mi House Sungai Long): Jalan SL 1/2 (`3.0398344, 101.7937589`, building). Hours: Tue–Sun 09:00–20:30 (Mon closed).
34. **`scan-mygei-cheong-fun-menu-9463e4`** (MY記腸粉 Sri Damansara): Jalan Tanjung SD 13 (`3.1864574, 101.6058952`, building). Hours: Tue–Fri 11:30–22:00; Sat–Sun 10:00–22:00 (Mon closed).
35. **`scan-pormtip-thai-restaurant-menu-14a1f6`** (PORMTIP THAI Permaisuri): Dataran Dwitasik (`3.1020615, 101.7128079`, building). Hours: Mon–Sun 12:15–23:30. **Last order: 23:30**.

### Checkpoint 4: ScanMenu Outlets (7 Outlets)
36. **`scan-shibuya-dessert-menu-b30866`** (Shibuya Dessert Sri Petaling): Jalan Radin Bagus 5 (`3.0704908, 101.6936012`, building). Hours: Tue–Sun 12:00–22:00 (Mon closed).
37. **`scan-soulja-premium-soya-menu-90722a`** (Soulja Soya KL Eco City): KL Eco City Mall (`3.1191391, 101.6741869`, building). Hours: Mon–Sun 10:00–22:00.
38. **`scan-sushi-zensai-sake-bar-menu-4a407b`** (Sushi Zensai KL Eco City): KL Eco City Mall (`3.1191391, 101.6741869`, building). Hours: Mon–Sat 12:00–22:00 (Sun closed).
39. **`scan-thai-chala-menu-24fda2`** (Thai Chala Sri Petaling): Jalan Radin Tengah (`3.0686960, 101.6910332`, building). Hours: Mon–Sun 11:30–22:30.
40. **`scan-theres-a-hot-pot-restaurant-menu-557005`** (There's A Hot Pot Happy Garden): Seri Gembira Avenue (`3.0794631, 101.6866761`, building). Hours: Mon–Sun 11:30–14:30 & 17:30–23:30.
41. **`scan-warung-makcik-kiah-menu-f13d31`** (Warung Makcik Kiah Sri Petaling): Jalan Radin Bagus 9 (`3.0686136, 101.6916322`, building). Hours: Mon–Sun 10:00–22:00.
42. **`scan-xin-hao-tat-restaurant-menu-c65b3b`** (Xin Hao Tat Puchong): Jalan Puteri 2/4 (`3.0215881, 101.6166298`, building). Hours: Mon–Sun 11:30–22:00.

### Checkpoint 5: ScanMenu Outlets (6 Outlets)
43. **`scan-man-kee-cafe-menu-0adc1b`** (文记冰室 Kepong): Jalan Metro Perdana 1 (`3.2173283, 101.6441220`, building). Hours: Mon–Sun 09:00–19:00.
44. **`scan-black-tower-coffee-menu-e23e6d`** (Black Tower Coffee Cheras): Dataran C180 (`3.0364302, 101.7660214`, building). Hours: Mon–Sun 11:00–23:00.
45. **`scan-chok-kar-chong-menu-5f1467`** (Chok Kar Chong Sungai Besi): Jalan 7/108C (`3.0934715, 101.7009861`, building). Hours: Mon–Sun 07:00–22:00.
46. **`scan-jia-li-mian-noodle-house-menu-4e3e36`** (Jia Li Mian Pandan Jaya): Jalan Pandan 2/2 (`3.1348508, 101.7409010`, building). Hours: Mon–Sun 07:30–16:00.
47. **`scan-jemi-cafe-menu-df9027`** (Jemi Cafe Taman Danau Desa): 17-1 Jalan 2/109F (`3.0993630, 101.6864845`, storefront). Hours: Wed–Mon 11:00–22:00 (Tue closed).
48. **`scan-moomin-bubbles-menu-ea04ac`** (Moomin Bubbles Sunway Pyramid): Sunway Pyramid (`3.0732800, 101.6074900`, building). Hours: Mon–Sun 10:00–22:00.

### Checkpoint 6: ScanMenu Outlets (6 Outlets)
49. **`scan-super-ramen-menu-9c56df`** (Super Ramen Sri Petaling): Jalan Radin Bagus (`3.0692952, 101.6943470`, building). Hours: Thu–Tue 12:00–15:00 & 18:00–23:00 (Wed closed). **Last order: 23:00**.
50. **`scan-nakamura-bashi-the-sphere-bangar-south-m-83c180`** (Nakamura Bashi Bangsar South): The Sphere (`3.1104931, 101.6675914`, building). Hours: Mon–Sun 11:00–21:30.
51. **`scan-dy-corner-menu-e9-b4-bb-e5-ae-9c-e9-a3-9-e02d03`** (D&Y Corner Taipan USJ 10): Jalan USJ 10/1C (`3.0484735, 101.5849685`, building). Hours: Mon–Sun 07:00–17:00.
52. **`scan-103-coffee-workshop-menu-6d4e54`** (103 Coffee Sri Petaling): Jalan Radin Bagus (`3.0692952, 101.6943470`, building). Hours: Mon–Sun 07:30–21:00.
53. **`scan-nasi-kandar-mamak-cafe-menu-danau-kota-160d92`** (Mamak Cafe Danau Kota): Jalan Danau Niaga 1 (`3.2026265, 101.7176163`, building). Hours: Mon–Sun 00:00–00:00 `closes_next_day: true` (24/7).
54. **`scan-nakamura-bashi-menu-ab95e6`** (Nakamura Bashi Puchong): Kompleks IOI Prima (`3.0448896, 101.6209345`, building). Hours: Mon–Sun 10:00–22:00.

### Checkpoint 7: ScanMenu Outlets (4 Outlets)
55. **`scan-khunthai-village-restaurant-menu-4aa842`** (Khunthai Cheras Jaya): Jalan CJ 1/6 (`3.0202680, 101.7671240`, building). Hours: Mon–Sun 11:00–00:00 `closes_next_day: true`.
56. **`scan-aroi-mak-mak-pj-menu-b9c07f`** (AROI Mak Mak Seksyen 17 PJ): Jalan 17/45 (`3.1195916, 101.6297602`, building). Hours: Tue–Thu 11:00–14:30 & 17:30–21:00; Fri–Sun 11:00–14:30 & 17:30–21:30 (Mon closed).
57. **`scan-hee-lai-ton-menu-puchong-9fee96`** (Hee Lai Ton Puchong): Jalan Kenari 1 (`3.0450856, 101.6201653`, building). Hours: Mon–Sun 11:30–14:30 & 17:30–22:00.
58. **`scan-viet-pho-cafe-menu-ec6e18`** (Viet Pho Cafe Balakong): Jalan Kasturi 1 (`3.0385945, 101.7691286`, building). Hours: Mon–Fri & Sun 10:30–22:30; Sat 11:00–23:00.

---

## 5. Summary of the 70 Outlets in Review Queue

The 70 unresolved outlets remain in `data/enrichment/location-hours.review.json` with detailed reason codes and candidate URLs:
1. **Unverified Hours / Missing Online Schedule (`hours_missing`: 55 outlets)**: ScanMenu-derived outlets whose operating hours could not be corroborated from official websites, social profiles, or verified directory listings.
2. **Approximate Coordinates (`coordinates_approximate`: 43 outlets)**: Outlets lacking indexed building or commercial block identifiers where only generic neighborhood/district coordinates were available.
3. **Confirmed Closures or Relocations (`possibly_closed`: 8 outlets)**:
   - `scan-banna-thai-menu-d5f27e`: Relocated from KLTS to Lucky Star / PV16 Setapak.
   - `scan-choy-kee-hong-kong-roasted-menu-7632fd`: Relocated from Taman Desa to Salak South Garden.
   - `scan-farid-fiery-chicken-menu-5d3f5a`: KL Traders Square branch closed.
   - `scan-golden-city-restaurant-menu-daa08e`: Sunway Nexis unit vacated.
   - `scan-lion-boba-menu-8c4d7b`: Damansara Utama outlet ceased operation.
   - `scan-shokudo-japanese-curry-rice-menu-e0b51f`: Replaced by Rinjin Shokudo in Taman Paramount.
   - `scan-astana-restaurant-menu-d01681`: Endah Promenade branch closed and relocated to TTDI.
   - `la-delima-bukit-jalil`: Replaced by Jihan Maju at Aked Esplanad.
4. **Irregular Off-Day Schedules (`hours_incomplete`: 6 outlets)**:
   - `scan-little-jungle-coffee-house-menu-e6ef4c`: Alternate-Thursday off-days.
   - `scan-vlite-cafe-menu-41e2a0`: Irregular lunar calendar off-days.
   - `scan-restoran-lim-mee-yoke-menu-80ca9d`: Ad-hoc off-days announced on social media.
   - `scan-kor-b-menu-20f668`: Biweekly Sunday off-days.
   - `scan-b-m-famous-yam-rice-menu-431f0c`: Biweekly Wednesday off-days.
   - `scan-dining-first-menu-6048b0`: Lacks defined weekly rest days.
5. **Chain & Brand Ambiguities (`ambiguous_branch`: 5 outlets)**:
   - `scan-petai-king-e8-87-ad-e8-b1-86-e7-8e-8b-me-2aac65` & `scan-petai-king-menu-62f3a9`: Duplicate entries for Petai King on Jalan Temenggung 27/9.
   - `scan-restoran-al-bidayah-menu-*` (3 branches): Multi-branch chain without individual branch separation in official channels.
6. **Contradictory Hours Published (`hours_conflict`: 4 outlets)**:
   - `mantra-bar-bangsar`: Typos and conflicting intervals on contact page.
   - `scan-restoran-bbq-thai-aroi-menu-ef470b`: Facebook page (4:30pm-2am) conflicts with dining directory (4pm-12am).
   - `scan-sakura-sushi-taman-midah-menu-76d604`: Conflicting continuous vs split service schedules.
   - `scan-chilli-village-restaurant-menu-e8-be-a3--e66d85`: Conflicting closing times across announcements.
7. **Address / Rename Overlaps (`possibly_renamed` & `address_mismatch`: 4 outlets)**:
   - `la-delima-bukit-jalil` and `scan-restoran-jihan-maju-menu-4d4cee`: Share exact unit No. 11-23-G Aked Esplanad.
   - `scan-kee-hiong-klang-bak-kut-teh-menu-2b0e47`: Catalog states SS2/67; active storefront operates on SS2/75.

---

## 6. Downstream Safety & Activation Rules

- All 58 proposed records are formatted in `data/enrichment/location-hours.patch.json` conforming to schema version 1.
- All 99 new sources in `data/enrichment/location-hours.sources.json` have `expires_at: null`, enforcing the fail-closed freshness gate until an operator defines a recheck deadline.
- Live recommendations and Chroma indexing remain inactive and safe until an operator explicitly applies and audits the reviewed patch.
