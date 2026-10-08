"""GC-1 golden fixture catalog and snapshot builders (docs/GOLDEN_RECOMMENDATION_TESTS.md §2).

Everything here is fictional. Times are relative to "now" so evidence stays fresh.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from dining.catalog.models import Catalog

MYT = ZoneInfo("Asia/Kuala_Lumpur")
SRC = "gc-src"
SRC_EXPIRED = "gc-src-expired"
ORIGIN = (3.1200, 101.6200)
KM_LAT = 1 / 111.195  # degrees of latitude per km

OUTLETS = {
    # id: (name, brand, cuisine, distance_km, halal, step_free)
    "G01": ("Kedai Sup Harmoni", "BR-SUP", "Malaysian", 0.3, "certified", None),
    "G02": ("Rasa Kari", "BR-KARI", "Indian", 0.6, "unknown", "accessible"),
    "G03": ("Wok & Noodle", "BR-WOK", "Chinese", 0.9, "not_halal", None),
    "G04": ("Ember Grill", "BR-EMBER", "Western", 1.2, "restaurant_claim", None),
    "G05": ("Trattoria Bunga", "BR-TRAT", "Italian", 1.5, "unknown", "inaccessible"),
    "G06": ("Baan Thai", "BR-BAAN", "Thai", 1.8, "restaurant_claim", None),
    "G07": ("Mamak Bistari", "BR-MAMAK", "Mamak", 2.1, "certified", None),
}

ITEMS = {
    # id: (outlet, name, attributes, RM, claims, ingredients, role)
    "I01": (
        "G01",
        "Chicken noodle soup",
        ["noodle_soup", "savoury", "mild", "light"],
        18,
        [],
        ["chicken", "noodles", "egg", "celery"],
        "main",
    ),
    "I02": (
        "G01",
        "Mixed vegetable soup",
        ["soup", "light", "none"],
        14,
        ["vegetarian", "vegan"],
        ["cabbage", "carrot", "tofu", "soy"],
        "main",
    ),
    "I03": (
        "G02",
        "Dhal rice set",
        ["rice", "savoury", "mild", "hearty"],
        16,
        ["vegetarian", "vegan"],
        ["rice", "lentils", "onion"],
        "main",
    ),
    "I04": (
        "G02",
        "Mutton curry rice",
        ["rice", "rich", "spicy", "hot", "hearty"],
        26,
        [],
        ["mutton", "rice", "chilli", "coconut"],
        "main",
    ),
    "I05": (
        "G03",
        "Wonton noodles",
        ["noodles", "savoury", "mild", "regular"],
        15,
        [],
        ["noodles", "pork", "prawn", "egg", "wheat"],
        "main",
    ),
    "I06": (
        "G03",
        "Tofu fried rice",
        ["rice", "savoury", "none", "regular"],
        13,
        ["vegetarian"],
        ["rice", "tofu", "egg", "soy"],
        "main",
    ),
    "I07": (
        "G04",
        "Grilled chicken chop",
        ["grill", "smoky", "none", "hearty"],
        34,
        [],
        ["chicken", "potato", "butter"],
        "main",
    ),
    "I08": (
        "G04",
        "Garden salad",
        ["salad", "light", "none"],
        24,
        ["vegetarian"],
        ["lettuce", "parmesan", "egg", "wheat"],
        "main",
    ),
    "I09": (
        "G05",
        "Mushroom cream pasta",
        ["pasta", "rich", "none", "regular"],
        29,
        ["vegetarian"],
        ["wheat", "mushroom", "milk", "parmesan"],
        "main",
    ),
    "I10": (
        "G05",
        "Margherita pizza",
        ["pizza", "savoury", "none", "regular"],
        31,
        ["vegetarian"],
        ["wheat", "tomato", "milk"],
        "main",
    ),
    "I11": (
        "G06",
        "Tom yum noodle soup",
        ["noodle_soup", "sour", "spicy", "hot", "regular"],
        21,
        [],
        ["prawn", "noodles", "chilli", "lemongrass"],
        "main",
    ),
    "I12": (
        "G06",
        "Pineapple fried rice",
        ["rice", "sweet", "mild", "regular"],
        20,
        [],
        ["rice", "prawn", "peanut", "pineapple", "egg"],
        "main",
    ),
    "I13": (
        "G07",
        "Roti canai with dhal",
        ["savoury", "mild", "light"],
        7,
        ["vegetarian"],
        ["wheat", "ghee", "lentils"],
        "main",
    ),
    "I14": (
        "G07",
        "Mee goreng mamak",
        ["noodles", "spicy", "medium", "regular"],
        11,
        [],
        ["noodles", "egg", "chicken", "chilli", "tomato"],
        "main",
    ),
    "I15": ("G07", "Teh tarik", [], 4, ["vegetarian"], ["tea", "milk"], "beverage"),
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def meal_at(days: int = 1, hh: int = 12, mm: int = 30) -> datetime:
    local_day = (now().astimezone(MYT) + timedelta(days=days)).date()
    return datetime.combine(local_day, time(hh, mm), tzinfo=MYT)


def _hours(opens, closes, last, closes_next=False, last_next=False):
    return [
        {
            "weekday": d,
            "opens": opens,
            "closes": closes,
            "closes_next_day": closes_next,
            "last_order": last,
            "last_order_next_day": last_next,
            "last_order_source_ids": [SRC],
        }
        for d in range(7)
    ]


def _source(sid, observed, expires):
    return {
        "source_id": sid,
        "url": "https://restaurant.example/golden-fixture",
        "kind": "synthetic",
        "observed_at": observed.isoformat(),
        "expires_at": expires.isoformat(),
        "publisher_updated_at": None,
        "evidence_text": "Fictional golden-test fixture; describes no real restaurant.",
        "rights": {
            "display": "allowed",
            "embed": "allowed",
            "basis": "Project-authored fixture.",
        },
    }


def _item(iid, outlet, name, attrs, rm, claims, ingredients, role, **extra):
    item = {
        "item_id": iid,
        "outlet_id": outlet,
        "name": name,
        "description": name,
        "variant": "Regular",
        "language": "en",
        "menu_version": "gc-1",
        "cuisine_tags": [OUTLETS[outlet][2]] if outlet in OUTLETS else ["Malaysian"],
        "attributes": attrs,
        "price": {
            "amount_minor": rm * 100,
            "currency": "MYR",
            "channel": "dine_in",
            "unit": "portion",
            "minimum_quantity": 1,
            "mandatory_charges_included": True,
            "all_mandatory_charges_known": True,
            "payable_amount_minor": rm * 100,
        },
        "meal_role": role,
        "serves_min": 1,
        "serves_max": 1,
        "review_status": "reviewed",
        "review_reasons": [],
        "ingredients": ingredients,
        "ingredients_complete": True,
        "allergens_present": [],
        "allergen_assessment": "unknown",
        "cross_contact_status": "unknown",
        "dietary_claims": claims,
        "live_availability": "available",
        "source_ids": [SRC],
    }
    item.update(extra)
    return item


def _outlet(oid, name, brand, cuisine, dist, halal, step_free, lat=None):
    t = now()
    halal_ev = {
        "status": halal,
        "certificate_id": None,
        "authority": None,
        "source_ids": [],
    }
    if halal != "unknown":
        halal_ev["source_ids"] = [SRC]
    if halal == "certified":
        halal_ev.update(certificate_id=f"FICT-{oid}", authority="Fictional registry")
    access = {}
    if step_free:
        access["step_free_entrance"] = {
            "status": step_free,
            "source_ids": [SRC],
            "review_status": "reviewed",
        }
    hours = (
        _hours("07:00", "23:00", "22:30")
        if oid == "G07"
        else _hours("10:00", "22:00", "21:30")
    )
    return {
        "outlet_id": oid,
        "brand_id": brand,
        "name": name,
        "address": f"Fictional address {oid}",
        "city": "Petaling Jaya",
        "state": "Selangor",
        "country": "MY",
        "latitude": lat if lat is not None else round(ORIGIN[0] + dist * KM_LAT, 6),
        "longitude": ORIGIN[1],
        "timezone": "Asia/Kuala_Lumpur",
        "cuisine_tags": [cuisine],
        "opening_hours": hours,
        "hours_status": "published",
        "holiday_exceptions_known": True,
        "opening_exceptions": [],
        "opening_exceptions_coverage": {
            "starts_on": (t - timedelta(days=30)).date().isoformat(),
            "ends_on": (t + timedelta(days=30)).date().isoformat(),
            "source_ids": [SRC],
        },
        "halal": halal_ev,
        "accessibility": access,
        "source_ids": [SRC],
    }


def base_raw() -> dict:
    t = now()
    brands = sorted({o[1] for o in OUTLETS.values()})
    return {
        "schema_version": "2",
        "catalog_id": "golden-gc-1",
        "version": "1",
        "generated_at": t.isoformat(),
        "synthetic": True,
        "brands": [{"brand_id": b, "name": b} for b in brands],
        "outlets": [_outlet(oid, *spec) for oid, spec in OUTLETS.items()],
        "menu_items": [_item(iid, *spec) for iid, spec in ITEMS.items()],
        "sources": [
            _source(SRC, t - timedelta(days=1), t + timedelta(days=14)),
            _source(SRC_EXPIRED, t - timedelta(days=10), t - timedelta(days=1)),
        ],
    }


def outlet(raw, oid):
    return next(o for o in raw["outlets"] if o["outlet_id"] == oid)


def item(raw, iid):
    return next(i for i in raw["menu_items"] if i["item_id"] == iid)


# ---- overlays (doc §2.5) -------------------------------------------------------
def ov_g08(raw):
    raw["outlets"].append(
        _outlet(
            "G08",
            "Kedai Sup Harmoni Branch 2",
            "BR-SUP",
            "Malaysian",
            0.5,
            "certified",
            None,
        )
    )
    for src, new in (("I01", "I81"), ("I02", "I82")):
        copy = deepcopy(item(raw, src))
        copy.update(item_id=new, outlet_id="G08")
        raw["menu_items"].append(copy)


def ov_g09(raw):
    raw["brands"].append({"brand_id": "BR-UTOPIA", "name": "BR-UTOPIA"})
    raw["outlets"].append(
        _outlet("G09", "Sup Utopia", "BR-UTOPIA", "Malaysian", 6.0, "unknown", None)
    )
    raw["menu_items"].append(
        _item(
            "I91",
            "G09",
            "Clear soup",
            ["soup", "light", "none"],
            10,
            ["vegetarian", "vegan"],
            ["water", "vegetables"],
            "main",
        )
    )


def ov_halal_expired(raw):
    outlet(raw, "G01")["halal"]["source_ids"] = [SRC_EXPIRED]


def ov_closed(raw, oid, on_date: date):
    outlet(raw, oid)["opening_exceptions"] = [
        {
            "on_date": on_date.isoformat(),
            "status": "closed",
            "intervals": [],
            "source_ids": [SRC],
        }
    ]


def ov_hours_unknown(raw, oid):
    target = outlet(raw, oid)
    target["hours_status"] = "unknown"
    target["opening_hours"] = []


def ov_sources_expired(raw, oid):
    outlet(raw, oid)["source_ids"] = [SRC_EXPIRED]
    for it in raw["menu_items"]:
        if it["outlet_id"] == oid:
            it["source_ids"] = [SRC_EXPIRED]


def ov_g07_night(raw):
    outlet(raw, "G07")["opening_hours"] = _hours(
        "18:00", "03:00", "02:30", closes_next=True, last_next=True
    )


def ov_platter(raw):
    raw["menu_items"].append(
        _item(
            "I16",
            "G01",
            "Family claypot soup",
            ["soup", "light", "none"],
            40,
            ["vegetarian", "vegan"],
            ["tofu", "vegetables"],
            "main",
            serves_min=2,
            serves_max=4,
        )
    )
    item(raw, "I16")["price"]["unit"] = "set"


def ov_charges_unknown(raw, *iids):
    for iid in iids:
        price = item(raw, iid)["price"]
        price.update(
            mandatory_charges_included=None,
            all_mandatory_charges_known=False,
            payable_amount_minor=None,
        )


def ov_dup(raw, src="I05", copies=100):
    base = item(raw, src)
    for n in range(copies):
        copy = deepcopy(base)
        copy["item_id"] = f"{src}-copy-{n}"
        raw["menu_items"].append(copy)


def ov_noise(raw, copies=50):
    base = item(raw, "I01")
    for n in range(copies):
        copy = deepcopy(base)
        copy.update(
            item_id=f"noise-{n}",
            name=f"Unrelated cake {n}",
            description="Unrelated cake",
            attributes=[],
            cuisine_tags=[],
        )
        raw["menu_items"].append(copy)


def catalog(raw=None) -> Catalog:
    return Catalog.model_validate(raw or base_raw())


# ---- snapshot builders ----------------------------------------------------------
def diner(profile=None, uid=None, **response):
    p = {
        "allergy_status": "none",
        "halal_policy": "none",
        "requirements_reviewed": True,
        "dietary_requirements": [],
    }
    p.update(profile or {})
    r = {"requirements_confirmed": True, "budget": 50}
    r.update(response)
    if r.get("budget", 0) is None:
        r.pop("budget")
    person = {"profile": p, "response": r}
    for key in (
        "observations",
        "venue_preferences",
        "route_estimates",
        "visit_history_complete",
    ):
        if key in p:
            person[key] = p.pop(key)
    if uid:
        person["user_id"] = uid
    return person


def snapshot(
    *people,
    at=None,
    duration=60,
    radius=5,
    lat=ORIGIN[0],
    lng=ORIGIN[1],
    confirmations=None,
):
    at = at or meal_at()
    snap = {
        "revision": 1,
        "meal": {
            "id": "golden",
            "meal_at": at.isoformat(),
            "duration_minutes": duration,
            "latitude": lat,
            "longitude": lng,
            "radius_km": radius,
        },
        "participants": [
            dict(p, user_id=p.get("user_id", f"P{n}")) for n, p in enumerate(people, 1)
        ],
    }
    if confirmations is not None:
        snap["preparation_confirmations"] = confirmations
    return snap


def confirmations(term, outlets=OUTLETS, expires=None, overrides=None):
    expires = expires or (meal_at() + timedelta(days=1))
    out = []
    for oid in outlets:
        exp = (overrides or {}).get(oid, expires)
        out.append(
            {
                "outlet_id": oid,
                "exact_bounded_claim": f"Kitchen confirmed the intended order is prepared without {term} and without shared {term} contact",
                "expires_at": exp.isoformat(),
            }
        )
    return out


def route(eta, expired=False):
    t = now()
    return {
        "eta_minutes": eta,
        "source_id": "golden-route",
        "observed_at": (t - timedelta(minutes=5)).isoformat(),
        "expires_at": (
            t - timedelta(minutes=1) if expired else t + timedelta(hours=1)
        ).isoformat(),
    }


def visit(oid, enjoyment="enjoyed", days_ago=1):
    return {
        "outlet_id": oid,
        "source": "confirmed_visit",
        "visited": True,
        "enjoyment": enjoyment,
        "created_at": (now() - timedelta(days=days_ago)).isoformat(),
    }
