"""Production golden suite: docs/GOLDEN_PRODUCTION_58_TESTS.md (P-001..P-107).

Runs against the production catalog (0.4.0-translated, approved 7 Oct 2026), the active
persistent Chroma index and the multilingual sentence-transformer. Skips when those
artefacts are not present.
Product decisions of 7 Oct 2026 are encoded as global invariants (see INVARIANTS below).
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dining.catalog.models import load_catalog
from dining.recommendation.engine import Recommender
from dining.recommendation.personal import score_personal_options

ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = ROOT / "var/catalog-import/kl-selangor-real-pilot-58-translated"
INDEX_DIR = ROOT / "var/vector/catalog"
MYT = ZoneInfo("Asia/Kuala_Lumpur")
QUARANTINED = "ditaliane-ioi-alfredo-funghi-fettuccine-3-pcs-beef-meatballs"
NON_MEAL = {"beverage", "dessert", "side", "add_on"}
SEEN_OUTLETS: set[str] = set()

if (
    not (CATALOG_DIR / "catalog.validated.json").exists()
    or not (INDEX_DIR / "active_index.json").exists()
):
    pytest.skip(
        "tagged catalog or active vector index not present", allow_module_level=True
    )

O = {
    "SR": "scan-super-ramen-menu-9c56df",
    "C103": "scan-103-coffee-workshop-menu-6d4e54",
    "GUI": "scan-restoran-gui-lin-sri-petaling-menu-a90c9d",
    "WMK": "scan-warung-makcik-kiah-menu-f13d31",
    "SHB": "scan-shibuya-dessert-menu-b30866",
    "GAGA": "scan-gaga-western-corner-menu-54b37b",
    "TCH": "scan-thai-chala-menu-24fda2",
    "ALH": "scan-al-haramain-restaurant-menu-b385ba",
    "HOT": "scan-theres-a-hot-pot-restaurant-menu-557005",
    "ZAK": "scan-zakuro-japanese-restaurant-menu-dbd35b",
    "CKC": "scan-chok-kar-chong-menu-5f1467",
    "MENYA": "scan-menya-yamato-e9-ba-b5-e5-b1-8b-e5-a4-a7--d7cfa9",
    "LAKE": "mamak-cafe-lake-city",
    "IMPIAN": "mamak-cafe-taman-impian",
    "BEAN": "scan-bean-jr-menu-68e934",
    "MANKEE": "scan-man-kee-cafe-menu-0adc1b",
    "TSUKIJI": "tsukiji-sushi-arkadia",
    "HUGH": "scan-151-hugh-low-kopitiam-nu-sentral-menu-c7e400",
    "BRICKM": "mamak-cafe-brickfields",
    "PRIME": "prime-kuala-lumpur",
    "LAMBO": "lambogrill-shah-alam",
    "JOM": "scan-jom-laksa-menu-57b2d4",
    "SOI": "soi55-ss15-subang-jaya",
    "SINGH": "big-singh-chapati-ss15",
    "MOOMIN": "scan-moomin-bubbles-menu-ea04ac",
    "JIALI": "scan-jia-li-mian-noodle-house-menu-4e3e36",
    "YAKI": "scan-yakitori-haki-menu-e067c1",
    "DEPINE": "scan-de-pine-cafe-menu-e0e601",
    "YTF": "scan-cheras-homey-yong-tau-foo-menu-100d39",
    "CILI": "cili-kampung-suria-klcc",
    "HISAR": "grand-hisar-stonor",
    "HAKKA": "hakka-raja-chulan",
    "HIDE": "hide-kl-ampang",
    "ONSE": "onsemiro-intermark",
    "BURGER": "myburgerlab-seapark",
    "GREEN": "green-view-pj",
    "AROI": "scan-aroi-mak-mak-pj-menu-b9c07f",
    "RK": "red-kettle-starling",
    "DIT": "ditaliane-ioi-mall-damansara",
    "VIET": "scan-viet-pho-cafe-menu-ec6e18",
    "BARBER": "bar-ber-cheras",
    "BT": "scan-black-tower-coffee-menu-e23e6d",
    "KHUN": "scan-khunthai-village-restaurant-menu-4aa842",
    "FEIPO": "scan-fei-po-ban-mee-menu-e8b249",
    "MIHOUSE": "scan-mi-house-menu-9d5721",
    "NAKAP": "scan-nakamura-bashi-menu-ab95e6",
    "HEE": "scan-hee-lai-ton-menu-puchong-9fee96",
    "DEF": "scan-de-forest-cafe-menu-f5b778",
    "XHT": "scan-xin-hao-tat-restaurant-menu-c65b3b",
    "DANAU": "scan-nasi-kandar-mamak-cafe-menu-danau-kota-160d92",
}
MP = {
    "PJ": (3.1200, 101.6250),
    "OKR": (3.0690, 101.6930),
    "KLCC": (3.1579, 101.7123),
    "CHERAS": (3.0364, 101.7660),
    "SS15": (3.0775, 101.5883),
    "PUCHONG": (3.0400, 101.6180),
    "KEPONG": (3.2050, 101.6600),
    "SHAHALAM": (3.0879, 101.5456),
    "PUDU": (3.1000, 101.7400),
    "BRICK": (3.1330, 101.6880),
    "PENANG": (5.4141, 100.3288),
}
DAYS = {d: n for n, d in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])}


@pytest.fixture(scope="module")
def env():
    from dining.retrieval.index import load_persistent_index

    catalog = load_catalog(CATALOG_DIR / "catalog.validated.json")
    allowed = set(
        json.loads((CATALOG_DIR / "activation-manifest.json").read_text())[
            "allowed_outlet_ids"
        ]
    )
    index = load_persistent_index(INDEX_DIR, catalog=catalog)
    assert index is not None, "active index does not match the tagged catalog"
    return {
        "catalog": catalog,
        "allowed": allowed,
        "index": index,
        "items": {i.item_id: i for i in catalog.menu_items},
    }


def when(spec: str) -> datetime:
    day, clock = spec.split()
    hh, mm = map(int, clock.split(":"))
    today = datetime.now(MYT).date() + timedelta(days=1)
    delta = (DAYS[day] - today.weekday()) % 7
    return datetime.combine(today + timedelta(days=delta), time(hh, mm), tzinfo=MYT)


def diner(profile=None, budget=60, **response):
    p = {
        "allergy_status": "none",
        "halal_policy": "none",
        "requirements_reviewed": True,
        "dietary_requirements": [],
    }
    p.update(profile or {})
    r = {"requirements_confirmed": True, "budget": budget}
    r.update(response)
    return {"profile": p, "response": r}


def cr(text, **k):
    return diner(craving=text, **k)


def cu(cuisine, **k):
    return diner(cuisines=[cuisine], **k)


class Run:
    def __init__(self, env, result, snap):
        self.env, self.result, self.snap = env, result, snap

    @property
    def order(self):
        return [o["outlet_id"] for o in self.result["options"]]

    @property
    def status(self):
        return self.result["status"]

    @property
    def verification(self):
        return {v["outlet_id"] for v in self.result["verification"]}

    def personal(self, n):
        person = self.snap["participants"][n - 1]
        return score_personal_options(
            {"profile": person["profile"], "response": person["response"]},
            self.result["options"],
            self.env["catalog"],
        )

    def item_name(self, item_id):
        return self.env["items"][item_id].name


def go(
    env, mp, radius, at="Thu 12:30", *people, dur=60, index=True, confirmations=None
):
    lat, lng = MP[mp]
    snap = {
        "revision": 1,
        "meal": {
            "id": "prod-golden",
            "meal_at": when(at).isoformat(),
            "duration_minutes": dur,
            "latitude": lat,
            "longitude": lng,
            "radius_km": radius,
        },
        "participants": [dict(p, user_id=f"P{n}") for n, p in enumerate(people, 1)],
    }
    if confirmations is not None:
        snap["preparation_confirmations"] = confirmations
    rec = Recommender(env["catalog"], embedding_index=env["index"] if index else None)
    r = Run(env, rec(snap), snap)
    invariants(r)
    return r


def invariants(r):
    """Global production invariants (doc §4 + decisions 1–2 of 7 Oct 2026)."""
    items, allowed = r.env["items"], r.env["allowed"]
    assigned = {m["id"] for o in r.result["options"] for m in o["menu_items"]}
    assert len(r.order) <= 3 and len(r.order) == len(set(r.order))
    assert set(r.order) <= allowed, f"outlet outside the 58: {set(r.order) - allowed}"
    SEEN_OUTLETS.update(r.order)
    assert QUARANTINED not in assigned
    bad_roles = {
        i: items[i].meal_role for i in assigned if items[i].meal_role in NON_MEAL
    }
    assert not bad_roles, (
        f"non-meal item assigned as a meal (decision 2): {[(items[i].name, role) for i, role in bad_roles.items()]}"
    )
    no_price = [items[i].name for i in assigned if items[i].price is None]
    assert not no_price, f"unpriced dish cleared a firm cap (decision 1): {no_price}"


def personal_ok(r, n, outlet_key=None, names=(), forbid=()):
    """Personal #1 at the given outlet with a dish whose name contains one of `names`."""
    recs = r.personal(n)
    assert recs, f"P{n} has no personal recommendation"
    pool = set(r.order)
    for rec in recs:
        item = r.env["items"][rec["item_id"]]
        assert rec["outlet_id"] in pool
        assert rec["item_id"] != QUARANTINED
        assert item.meal_role not in NON_MEAL, (
            f"P{n} personal {item.name!r} is a {item.meal_role}"
        )
        assert item.price is not None, f"P{n} personal {item.name!r} has no price"
        budget = r.snap["participants"][n - 1]["response"].get("budget")
        if budget is not None and item.price is not None:
            amount = item.price.payable_amount_minor or item.price.amount_minor
            assert amount <= budget * 100, (
                f"P{n} personal {item.name!r} RM{amount / 100} > cap RM{budget}"
            )
        assert not any(f.casefold() in item.name.casefold() for f in forbid), (
            f"P{n} personal {item.name!r} is forbidden"
        )
    top = recs[0]
    if outlet_key:
        allowed = {
            O[k]
            for k in (outlet_key if isinstance(outlet_key, tuple) else (outlet_key,))
        }
        assert top["outlet_id"] in allowed, (
            f"P{n} personal #1 at {top['outlet_id']}, expected {allowed}"
        )
    if names:
        name = r.item_name(top["item_id"]).casefold()
        assert any(x.casefold() in name for x in names), (
            f"P{n} personal #1 dish {r.item_name(top['item_id'])!r} not in {names}"
        )


def top1(r, *keys):
    assert r.order, f"no options (status {r.status})"
    assert r.order[0] in {O[k] for k in keys}, (
        f"#1 is {r.order[0]}, expected one of {[O[k] for k in keys]}"
    )


def include(r, *keys):
    missing = [k for k in keys if O[k] not in r.order]
    assert not missing, f"missing from shortlist: {missing}; got {r.order}"


def exclude(r, *keys):
    present = [k for k in keys if O[k] in r.order]
    assert not present, f"must not be shortlisted: {present}"


def subset(r, *keys):
    extra = set(r.order) - {O[k] for k in keys}
    assert not extra, f"unexpected outlets: {extra}"


# ---------------------------------------------------------------- A. relevance
def test_p_001(env):
    r = go(env, "OKR", 3, "Thu 12:30", cr("ramen"), cr("ramen"))
    top1(r, "SR")
    personal_ok(r, 1, "SR", ("ramen",))


def test_p_002(env):
    r = go(env, "KEPONG", 4, "Thu 19:00", cr("ramen"), cr("ramen"))
    top1(r, "MENYA")
    personal_ok(r, 1, "MENYA", ("ramen", "tsukemen"))


def test_p_003(env):
    r = go(env, "KEPONG", 4, "Thu 19:00", cr("拉面"), cr("拉面"))
    top1(r, "MENYA")


def test_p_004(env):
    r = go(env, "BRICK", 1, "Thu 12:30", cr("nasi lemak"), cr("nasi lemak"))
    top1(r, "HUGH")
    personal_ok(r, 1, "HUGH", ("nasi lemak",))


def test_p_005(env):
    r = go(env, "SHAHALAM", 6, "Thu 12:30", cr("nasi lemak"), cr("nasi lemak"))
    top1(r, "LAMBO")
    personal_ok(r, 1, "LAMBO", ("nasi lemak",))


def test_p_006(env):
    r = go(env, "SS15", 3, "Thu 12:30", cr("laksa"), cr("laksa"))
    top1(r, "JOM")
    personal_ok(r, 1, "JOM", ("laksa",))


def test_p_007(env):
    r = go(env, "PUDU", 5, "Thu 12:30", cr("curry mee"), cr("curry mee"))
    top1(r, "JIALI")
    personal_ok(r, 1, "JIALI", ("curry mee",))


def test_p_008(env):
    r = go(env, "PUDU", 5, "Thu 12:30", cr("mi kari"), cr("mi kari"))
    top1(r, "JIALI")


def test_p_009(env):
    r = go(env, "KLCC", 2, "Thu 12:30", cu("Malay"), cu("Malay"))
    top1(r, "CILI")
    personal_ok(r, 1, "CILI", ("ayam", "sup", "masak"), forbid=("pisang",))


def test_p_010(env):
    r = go(env, "KLCC", 2, "Thu 12:30", cu("Turkish"), cu("Turkish"))
    top1(r, "HISAR")
    personal_ok(
        r,
        1,
        "HISAR",
        ("gözleme", "kebab", "scrambled", "shish", "köfte", "börek", "meze"),
        forbid=("coffee", "ayran", "baklava", "kunafa", "pudding"),
    )


def test_p_011(env):
    r = go(env, "KLCC", 2, "Thu 12:30", cu("Hakka"), cu("Hakka"))
    top1(r, "HAKKA")
    personal_ok(r, 1, "HAKKA", forbid=("[large]", "abalone", "clay pot"))


def test_p_012(env):
    r = go(env, "PJ", 2, "Thu 12:30", cr("burger"), cr("burger"))
    top1(r, "BURGER")
    personal_ok(r, 1, "BURGER")


def test_p_013(env):
    r = go(env, "PJ", 2, "Thu 12:30", cu("Thai"), cu("Thai"))
    top1(r, "AROI")
    personal_ok(r, 1, "AROI")


def test_p_014(env):
    r = go(env, "PJ", 2, "Thu 12:30", cr("seafood"), cr("seafood"))
    top1(r, "GREEN")
    personal_ok(r, 1, "GREEN")


def test_p_015(env):
    # Updated for decision 2: waffles/French toast are desserts, so Red Kettle can only offer a non-dessert item.
    r = go(env, "PJ", 2, "Thu 12:30", cr("waffle brunch"), cr("waffle brunch"))
    assert r.status == "shortlisted"
    personal_ok(r, 1, forbid=("latte", "mocha", "espresso", "waffle"))


def test_p_016(env):
    r = go(env, "CHERAS", 1, "Thu 12:30", cr("beef pho"), cr("beef pho"))
    top1(r, "VIET")
    personal_ok(r, 1, "VIET", ("河粉",))


def test_p_017(env):
    r = go(env, "CHERAS", 2, "Thu 12:30", cu("Thai"), cu("Thai"))
    top1(r, "KHUN")


def test_p_018(env):
    r = go(env, "CHERAS", 1, "Thu 12:30", cr("pizza"), cr("pizza"))
    top1(r, "BARBER", "BT")
    personal_ok(r, 1, ("BARBER", "BT"), ("pizza", "披萨", "比萨"))


def test_p_019(env):
    r = go(env, "PUCHONG", 3, "Thu 12:30", cr("sushi"), cr("sushi"))
    top1(r, "NAKAP")
    personal_ok(r, 1, "NAKAP", ("寿司", "手卷", "sushi"))


def test_p_020(env):
    r = go(env, "PUCHONG", 3, "Thu 12:30", cr("Hokkien mee"), cr("Hokkien mee"))
    top1(r, "XHT")
    personal_ok(r, 1, "XHT", ("福建面",))


def test_p_021(env):
    r = go(
        env, "PUCHONG", 3, "Thu 12:30", cr("fish ball noodles"), cr("fish ball noodles")
    )
    top1(r, "DEF", "XHT")
    include(r, "DEF", "XHT")


def test_p_022(env):
    r = go(env, "KEPONG", 3, "Thu 12:30", cr("char siu rice"), cr("char siu rice"))
    top1(r, "MANKEE")
    personal_ok(r, 1, "MANKEE", ("叉燒飯",))


def test_p_023(env):
    # Updated for decision 2: 豆花 is a dessert, never a meal; the shortlist must still give a real meal.
    r = go(env, "KEPONG", 3, "Thu 12:30", cr("tau fu fa"), cr("tau fu fa"))
    assert r.status == "shortlisted"
    personal_ok(r, 1, forbid=("豆花",))


def test_p_024(env):
    r = go(env, "PUDU", 1, "Thu 12:30", cr("yong tau foo"), cr("yong tau foo"))
    top1(r, "YTF")
    personal_ok(r, 1, "YTF", ("yong tau foo",))


def test_p_025(env):
    r = go(env, "PUDU", 1, "Thu 12:30", cr("yakitori"), cr("yakitori"))
    top1(r, "YAKI")
    personal_ok(r, 1, "YAKI", ("yakitori",))


def test_p_026(env):
    r = go(env, "OKR", 1, "Thu 12:30", cr("Arabic food"), cr("Arabic food"))
    top1(r, "ALH")
    personal_ok(
        r,
        1,
        "ALH",
        ("arayis", "madhghout", "aqdah", "haneeth", "saltah", "bamiah", "karhai"),
    )


def test_p_027(env):
    r = go(env, "OKR", 1, "Thu 12:30", cr("tom yam"), cr("tom yam"))
    top1(r, "WMK", "TCH")
    personal_ok(r, 1, ("WMK", "TCH"), ("tomyam", "tom yam"))


def test_p_028(env):
    r = go(env, "OKR", 2, "Thu 12:30", cr("hot pot steamboat"), cr("hot pot steamboat"))
    top1(r, "HOT")
    personal_ok(r, 1, "HOT", forbid=("三人",))


def test_p_029(env):
    r = go(env, "OKR", 1, "Thu 12:30", cr("chicken chop"), cr("chicken chop"))
    top1(r, "GAGA")
    personal_ok(r, 1, "GAGA", ("雞扒",))


def test_p_030(env):
    r = go(env, "SS15", 1, "Thu 12:30", cr("biryani"), cr("biryani"))
    top1(r, "SINGH")
    personal_ok(r, 1, "SINGH", ("briyani", "biryani"))


# ---------------------------------------------------------------- B. mixed cravings
def test_p_031(env):
    r = go(
        env,
        "OKR",
        1,
        "Thu 12:30",
        cr("ramen"),
        cr("nasi goreng"),
        cr("kakigori dessert"),
    )
    include(r, "SR", "WMK", "SHB")
    personal_ok(r, 1, "SR", ("ramen",))
    personal_ok(r, 2, "WMK", ("nasi goreng",))
    personal_ok(
        r, 3, "SHB", ("sandwich",)
    )  # decision 2: kakigori is a dessert, so P3's meal is a sandwich


def test_p_032(env):
    r = go(env, "KLCC", 1.2, "Thu 12:30", cu("Malay"), cu("Turkish"), cu("Chinese"))
    include(r, "CILI", "HAKKA", "HISAR")
    personal_ok(r, 1, "CILI")
    personal_ok(r, 2, "HISAR")
    personal_ok(r, 3, "HAKKA")


def test_p_033(env):
    r = go(env, "PJ", 2, "Thu 12:30", cr("burger"), cu("Thai"))
    include(r, "BURGER", "AROI")
    personal_ok(r, 1, "BURGER")
    personal_ok(r, 2, "AROI")


def test_p_034(env):
    r = go(
        env,
        "SS15",
        2.5,
        "Thu 12:30",
        cr("laksa"),
        cr("biryani"),
        cr("pad kra pao"),
        cr("bubble tea"),
    )
    include(r, "SOI", "SINGH", "JOM")
    exclude(r, "MOOMIN")
    personal_ok(r, 1, "JOM", ("laksa",))
    personal_ok(r, 2, "SINGH", ("briyani",))
    personal_ok(r, 3, "SOI", ("pad kra pao",))


def test_p_035(env):
    r = go(env, "CHERAS", 1, "Thu 12:30", cr("beef pho"), cr("pizza"))
    include(r, "VIET")
    assert O["BARBER"] in r.order or O["BT"] in r.order


def test_p_036(env):
    r = go(
        env,
        "PUCHONG",
        2.5,
        "Thu 12:30",
        cr("sushi"),
        cr("Hokkien mee"),
        cr("chicken chop"),
    )
    include(r, "NAKAP", "DEF", "XHT")


def test_p_037(env):
    r = go(
        env,
        "KEPONG",
        3,
        "Thu 12:30",
        cr("char siu rice"),
        cr("cheese naan"),
        cr("tau fu fa"),
    )
    include(r, "MANKEE", "IMPIAN")
    exclude(r, "LAKE")
    personal_ok(r, 1, "MANKEE", ("叉燒飯",))
    personal_ok(r, 2, "IMPIAN", ("naan",))


def test_p_038(env):
    r = go(env, "BRICK", 0.5, "Thu 12:30", cr("nasi lemak"), cr("roti canai"))
    include(r, "HUGH", "BRICKM")
    assert r.order[0] != O["PRIME"]


def test_p_039(env):
    r = go(
        env,
        "KLCC",
        1.2,
        "Thu 19:30",
        cu("Malay"),
        cu("Korean"),
        cu("Turkish"),
        cu("Japanese"),
    )
    subset(r, "CILI", "HAKKA", "HISAR")
    exclude(r, "HIDE", "ONSE")


def test_p_040(env):
    r = go(env, "OKR", 1, "Thu 12:30", cr("anything"), cr("anything"))
    assert r.status == "shortlisted"
    personal_ok(r, 1)


def test_p_041(env):
    r = go(
        env,
        "PJ",
        5,
        "Thu 12:30",
        diner({"dietary_requirements": ["vegetarian"]}),
        cr("seafood"),
    )
    assert r.order == [O["DIT"]]
    personal_ok(r, 1, "DIT", ("plant-based",))


def test_p_042(env):
    r = go(env, "OKR", 1, "Thu 12:30", cr("tom yam"), cr("chicken chop, not spicy"))
    include(r, "GAGA")
    assert O["WMK"] in r.order or O["TCH"] in r.order


# ---------------------------------------------------------------- C. hard requirements
def _blocked(r, status="needs_verification"):
    assert r.status == status and r.order == [], f"{r.status} {r.order}"


def test_p_043(env):
    _blocked(
        go(env, "KLCC", 2, "Thu 12:30", diner({"halal_policy": "certified"}), diner())
    )


def test_p_044(env):
    _blocked(
        go(env, "SS15", 2, "Thu 12:30", diner({"halal_policy": "certified"}), diner())
    )


def test_p_045(env):
    _blocked(
        go(env, "KLCC", 2, "Thu 12:30", diner({"halal_policy": "review"}), diner())
    )


def test_p_046(env):
    _blocked(
        go(env, "OKR", 1, "Thu 12:30", diner({"halal_policy": "unknown"}), diner())
    )


def test_p_047(env):
    r = go(
        env,
        "PJ",
        5,
        "Thu 12:30",
        diner({"dietary_requirements": ["vegetarian"]}),
        diner(),
    )
    assert r.order == [O["DIT"]]
    assert O["AROI"] not in r.order
    personal_ok(r, 1, "DIT", ("plant-based",))


def test_p_048(env):
    _blocked(
        go(
            env,
            "OKR",
            3,
            "Thu 12:30",
            diner({"dietary_requirements": ["vegetarian"]}),
            diner(),
        )
    )


def test_p_049(env):
    _blocked(
        go(
            env,
            "PJ",
            5,
            "Thu 12:30",
            diner({"dietary_requirements": ["vegan"]}),
            diner(),
        )
    )


def test_p_050(env):
    r = go(
        env,
        "OKR",
        1,
        "Thu 12:30",
        diner({"allergy_status": "declared", "allergens": ["peanut"]}),
        diner(),
    )
    _blocked(r)
    assert "peanut" not in json.dumps(r.result, default=str).casefold()


def test_p_051(env):
    _blocked(go(env, "OKR", 1, "Thu 12:30", diner(avoid=["pork"]), diner()))


def test_p_052(env):
    conf = [
        {
            "outlet_id": O["GREEN"],
            "exact_bounded_claim": "Kitchen confirmed Roasted Sha Tin Chicken is prepared without shellfish and without shared shellfish contact",
            "expires_at": (when("Thu 12:30") + timedelta(days=1)).isoformat(),
        }
    ]
    r = go(
        env,
        "PJ",
        2,
        "Thu 12:30",
        diner({"allergy_status": "declared", "allergens": ["shellfish"]}),
        diner(),
        confirmations=conf,
    )
    assert r.order == [O["GREEN"]], r.order
    assert {O["AROI"], O["BURGER"], O["RK"]} <= r.verification, (
        "other outlets must stay visible under Needs confirmation"
    )


def test_p_053(env):
    _blocked(
        go(
            env,
            "KLCC",
            2,
            "Thu 12:30",
            diner({"accessibility_requirements": ["step_free_entrance"]}),
            diner(),
        )
    )


def test_p_054(env):
    r = go(env, "KLCC", 2, "Thu 12:30", diner(occasion_features=["quiet"]), diner())
    assert r.status == "shortlisted" and r.order[0] == O["CILI"]


def test_p_055(env):
    _blocked(
        go(env, "OKR", 1, "Thu 12:30", diner(), diner({"requirements_reviewed": False}))
    )


def test_p_056(env):
    _blocked(go(env, "OKR", 1, "Thu 12:30", diner()), status="needs_input")


def test_p_057(env):
    _blocked(go(env, "PENANG", 5, "Thu 12:30", diner(), diner()), status="no_options")


def test_p_058(env):
    r = go(env, "KLCC", 0.5, "Thu 12:30", diner(), diner())
    assert r.order == [O["CILI"]]


def test_p_059(env):
    r = go(
        env, "PJ", 5, "Thu 12:30", cr("fettuccine alfredo"), cr("fettuccine alfredo")
    )
    top1(r, "DIT")
    personal_ok(r, 1, "DIT", ("alfredo", "fettuccine", "penne", "pasta"))


def test_p_060(env):
    r = go(env, "BRICK", 6, "Thu 12:30", cr("nasi kandar"), cr("nasi kandar"))
    assert set(r.order) <= env["allowed"]
    d = go(env, "KLCC", 6, "Thu 12:30", cr("dim sum"), cr("dim sum"))
    assert set(d.order) <= env["allowed"]


# ---------------------------------------------------------------- D. budget & price
def test_p_061(env):
    r = go(env, "OKR", 1, "Thu 12:30", diner(budget=8), diner(budget=8))
    top1(r, "ALH")
    personal_ok(r, 1, "ALH")


def test_p_062(env):
    r = go(env, "BRICK", 1, "Thu 12:30", diner(budget=5), diner(budget=5))
    top1(r, "BRICKM")
    exclude(r, "PRIME")
    personal_ok(r, 1, "BRICKM", ("roti",))


def test_p_063(env):
    r = go(env, "BRICK", 1, "Thu 12:30", cr("steak"), cr("steak"))
    exclude(r, "PRIME")


def test_p_064(env):
    r = go(
        env, "BRICK", 1, "Thu 12:30", cr("steak", budget=400), cr("steak", budget=400)
    )
    top1(r, "PRIME")
    personal_ok(r, 1, "PRIME", ("220g",))


def test_p_065(env):
    r = go(env, "PJ", 2, "Thu 12:30", cr("sang har noodle"), cr("sang har noodle"))
    top1(r, "GREEN")
    personal_ok(r, 1, "GREEN")


def test_p_066(env):
    r = go(env, "KLCC", 1.2, "Thu 12:30", cu("Korean"), cu("Korean"))
    exclude(r, "ONSE")
    assert O["ONSE"] in r.verification


def test_p_067(env):
    r = go(
        env,
        "KLCC",
        1,
        "Thu 19:30",
        cu("Japanese", budget=100),
        cu("Japanese", budget=100),
    )
    exclude(r, "HIDE")


def test_p_068(env):
    a = go(env, "KEPONG", 4, "Thu 19:00", cr("sushi"), cr("sushi"))
    assert not a.order or a.order[0] != O["TSUKIJI"]
    b = go(
        env, "KEPONG", 4, "Thu 19:00", cr("sushi", budget=150), cr("sushi", budget=150)
    )
    top1(b, "TSUKIJI")
    personal_ok(b, 1, "TSUKIJI", ("don",))


def test_p_069(env):
    p = cr("noodles", soft_budget_target=15)
    r = go(env, "OKR", 1, "Thu 12:30", p, cr("noodles", soft_budget_target=15))
    top = r.personal(1)[0]
    item = env["items"][top["item_id"]]
    assert (item.price.payable_amount_minor or item.price.amount_minor) <= 1500, (
        f"{item.name} above comfortable RM15"
    )


def test_p_070(env):
    r = go(
        env, "KLCC", 1, "Thu 12:30", cu("Chinese", budget=40), cu("Chinese", budget=40)
    )
    top1(r, "HAKKA")
    personal_ok(r, 1, "HAKKA", forbid=("[large]", "clay pot"))


# ---------------------------------------------------------------- E. hours
def test_p_071(env):
    r = go(env, "OKR", 3, "Sat 08:00", diner(), diner())
    subset(r, "C103", "CKC")
    assert r.order


def test_p_072(env):
    r = go(env, "BRICK", 1, "Fri 01:00", diner(), diner())
    assert r.order == [O["BRICKM"]]


def test_p_073(env):
    r = go(env, "CHERAS", 1, "Thu 23:30", diner(), diner())
    assert r.order == [O["BARBER"]]


def test_p_074(env):
    r = go(
        env,
        "KLCC",
        1.2,
        "Mon 19:30",
        cu("Japanese", budget=800),
        cu("Japanese", budget=800),
    )
    exclude(r, "HIDE")


def test_p_075(env):
    r = go(env, "PJ", 2, "Thu 15:30", cr("seafood"), cr("seafood"))
    subset(r, "BURGER", "RK")
    exclude(r, "GREEN", "AROI")


def test_p_076(env):
    r = go(env, "OKR", 3, "Wed 12:30", cr("ramen"), cr("ramen"))
    exclude(r, "SR")


def test_p_077(env):
    r = go(env, "CHERAS", 4, "Mon 12:30", cr("wonton noodles"), cr("wonton noodles"))
    exclude(r, "MIHOUSE")


def test_p_078(env):
    r = go(env, "PUDU", 1, "Tue 12:30", cr("yong tau foo"), cr("yong tau foo"))
    exclude(r, "YTF", "DEPINE")


def test_p_079(env):
    r = go(env, "KEPONG", 2.5, "Sat 03:00", diner(), diner())
    top1(r, "IMPIAN")
    exclude(r, "LAKE")


def test_p_080(env):
    r = go(env, "PJ", 2, "Thu 21:30", diner(), diner())
    subset(r, "RK")
    exclude(r, "GREEN", "BURGER", "AROI")


# ---------------------------------------------------------------- F. personal best fit
def test_p_081(env):
    r = go(env, "OKR", 1, "Thu 12:30", cr("ramen"), cr("tom yam"))
    personal_ok(r, 1, "SR", ("ramen",))
    personal_ok(r, 2, ("WMK", "TCH"), ("tomyam", "tom yam"))


def test_p_082(env):
    r = go(
        env, "CHERAS", 4, "Thu 12:30", cr("wonton noodles"), cr("pineapple fried rice")
    )
    top1(r, "MIHOUSE")
    personal_ok(r, 1, "MIHOUSE", ("云吞面",))
    personal_ok(r, 2, "MIHOUSE", ("菠萝炒饭",))


def test_p_083(env):
    r = go(
        env,
        "KEPONG",
        3,
        "Thu 12:30",
        cr("char siu rice"),
        cr("salt-baked chicken rice"),
    )
    top1(r, "MANKEE")
    personal_ok(r, 1, "MANKEE", ("叉燒飯",))
    personal_ok(r, 2, "MANKEE", ("手撕雞飯",))


def test_p_084(env):
    r = go(
        env,
        "PJ",
        5,
        "Thu 12:30",
        diner({"dietary_requirements": ["vegetarian"]}),
        cr("pasta"),
    )
    top1(r, "DIT")
    personal_ok(r, 1, "DIT", ("plant-based",))


def test_p_085(env):
    r = go(env, "KLCC", 1.2, "Thu 12:30", cu("Malay", budget=20), cu("Malay"))
    top1(r, "CILI")
    personal_ok(r, 1, "CILI", ("ayam kunyit", "sup ayam"))


def test_p_086(env):
    r = go(env, "PJ", 2, "Thu 12:30", cr("coffee"), cr("burger"))
    include(r, "BURGER")
    personal_ok(r, 1, forbid=("latte", "mocha", "espresso", "coffee", "cappuccino"))


def test_p_087(env):
    r = go(env, "SHAHALAM", 6, "Thu 12:30", cr("sambal pedas"), diner())
    top1(r, "LAMBO")
    personal_ok(r, 1, "LAMBO", forbid=("siakap sambal pedas",))


def test_p_088(env):
    r = go(env, "PJ", 5, "Thu 12:30", cr("alfredo"), cr("alfredo"))
    top1(r, "DIT")
    personal_ok(r, 1, "DIT", ("alfredo",))


def test_p_089(env):
    r = go(env, "PJ", 2, "Thu 12:30", cr("sang har noodle"), diner())
    top1(r, "GREEN")
    personal_ok(r, 1, "GREEN", forbid=("sang har noodle", "e-fu noodle"))


def test_p_090(env):
    r = go(
        env,
        "SS15",
        1,
        "Thu 12:30",
        cr("pad kra pao beef"),
        cr("pad kra pao seafood mama noodles"),
    )
    top1(r, "SOI")
    personal_ok(r, 1, "SOI", ("beef",))
    personal_ok(r, 2, "SOI", ("seafood mama",))


def test_p_091(env):
    r = go(env, "BRICK", 1, "Thu 12:30", cr("wagyu steak", budget=400), diner())
    top1(r, "PRIME")
    personal_ok(r, 1, "PRIME", ("220g",))


def test_p_092(env):
    r = go(env, "KLCC", 1.2, "Thu 12:30", cr("anything"), cr("anything"))
    assert r.order[0] == O["CILI"]
    personal_ok(r, 1, "CILI", forbid=("pisang",))


def test_p_093(env):
    r = go(
        env,
        "OKR",
        1,
        "Thu 12:30",
        cr("ramen"),
        cr("nasi goreng"),
        cr("kakigori dessert"),
    )
    for n in (1, 2, 3):
        assert {x["outlet_id"] for x in r.personal(n)} <= set(r.order)


def test_p_094(env):
    a = go(env, "OKR", 1, "Thu 12:30", cr("ramen"), cr("tom yam"))
    b = go(env, "OKR", 1, "Thu 12:30", cr("ramen"), cr("tom yam"))
    c = go(env, "OKR", 1, "Thu 12:30", cr("tom yam"), cr("ramen"))
    assert a.order == b.order
    assert a.personal(1) == b.personal(1) == c.personal(2)


def test_p_095(env):
    r = go(env, "OKR", 1, "Thu 12:30", cr("ramen"), cr("tom yam"))
    shared = json.dumps(r.result, default=str).casefold()
    assert "ramen" not in shared.replace("super ramen", "") or "tom yam" not in shared
    assert "my_personal_recommendations" not in shared and "reason_codes" not in shared


# ---------------------------------------------------------------- G. retrieval integrity
def test_p_096(env):
    r = go(env, "OKR", 3, "Thu 12:30", cr("ramen"), cr("ramen"))
    assert r.result["retrieval_status"] == "semantic"
    assert "paraphrase-multilingual-MiniLM-L12-v2" in (
        r.result.get("embedding_model") or ""
    )
    assert (
        env["index"].indexed_item_count == 1073
        and env["index"].indexed_outlet_count == 58
    )


def test_p_097(env):
    r = go(env, "OKR", 3, "Thu 12:30", cr("ramen"), cr("ramen"), index=False)
    assert (
        r.result["retrieval_status"] == "structured_fallback"
        and r.result.get("embedding_model") is None
    )


def test_p_098(env):
    r = go(env, "OKR", 1, "Thu 12:30", cr("pizza"), cr("pizza"))
    top1(r, "C103")
    personal_ok(r, 1, "C103", ("pizza",))


def test_p_099(env):
    r = go(
        env, "OKR", 1, "Thu 12:30", cr("something for my diabetes, low sugar"), diner()
    )
    text = json.dumps(r.result, default=str).casefold()
    assert "diabetes" not in text and "low sugar" not in text


# ---------------------------------------------------------------- H. majority preference
# Added 7 Oct 2026 (product owner): when most of the group wants the same thing, the
# group's top choice serves that majority, unless a hard requirement or the fairness floor
# says otherwise. Each minority diner's own wish should still appear where it is available.
def test_p_101(env):
    r = go(env, "KLCC", 1.2, "Thu 12:30", cu("Malay"), cu("Malay"), cu("Turkish"))
    top1(r, "CILI")
    include(r, "HISAR")
    personal_ok(r, 1, "CILI")
    personal_ok(r, 3, "HISAR")


def test_p_102(env):
    r = go(env, "KLCC", 1.2, "Thu 12:30", cu("Malay"), cu("Malay"), cu("Japanese"))
    top1(r, "CILI")
    personal_ok(r, 2, "CILI")


def test_p_103(env):
    r = go(env, "KLCC", 1.2, "Thu 12:30", cu("Malay"), cu("Malay"), cu("Chinese"))
    top1(r, "CILI")
    include(r, "HAKKA")
    personal_ok(r, 3, "HAKKA")


def test_p_104(env):
    r = go(env, "KLCC", 1.2, "Thu 12:30", cu("Malay"), cu("Malay"), cu("Korean"))
    top1(r, "CILI")
    exclude(r, "ONSE")
    assert O["ONSE"] in r.verification


def test_p_105(env):
    r = go(env, "OKR", 1, "Thu 12:30", cr("ramen"), cr("ramen"), cr("nasi goreng"))
    top1(r, "SR")
    include(r, "WMK")
    personal_ok(r, 1, "SR", ("ramen",))
    personal_ok(r, 3, "WMK", ("nasi goreng",))


def test_p_106(env):
    r = go(env, "PJ", 2, "Thu 12:30", cu("Thai"), cu("Thai"), cu("Thai"), cr("burger"))
    top1(r, "AROI")
    include(r, "BURGER")
    personal_ok(r, 4, "BURGER")


def test_p_107(env):
    # A majority never overrides one person's hard requirement (no outlet has a halal certificate).
    r = go(
        env,
        "KLCC",
        1.2,
        "Thu 12:30",
        cu("Malay"),
        cu("Malay"),
        diner({"halal_policy": "certified"}, cuisines=["Malay"]),
    )
    _blocked(r)


def test_p_100(env):
    assert SEEN_OUTLETS, (
        "run the whole module so earlier cases populate the scope check"
    )
    assert SEEN_OUTLETS <= env["allowed"]
