"""FastMCP server exposing restaurant data, vibe search, and reviews."""

import json
import importlib
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastmcp import FastMCP


mcp = FastMCP("Connoisseur-Server")
DATA_DIR = Path(__file__).resolve().parent


def _existing_data_path(*names: str) -> Path:
    """Find the first existing file, supporting lab and project filenames."""
    for name in names:
        path = DATA_DIR / name
        if path.is_file():
            return path
    expected = " or ".join(names)
    raise FileNotFoundError(f"Could not find {expected} in {DATA_DIR}")


CULINARY_MAP_PATH = _existing_data_path("California-Culinary-Map.txt")
RESTAURANT_DATA_PATH = _existing_data_path(
    "structured-restaurant-data.json",
    "structured_restaurant_data.json",
)
REVIEW_DATA_PATH = _existing_data_path(
    "augmented-user-review.json",
    "augmented_user_review.json",
)


@lru_cache(maxsize=1)
def load_restaurant_data() -> list[dict[str, Any]]:
    """Load the structured restaurant data produced in Module 1."""
    with RESTAURANT_DATA_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"Expected a list in {RESTAURANT_DATA_PATH}")
    return data


@lru_cache(maxsize=1)
def load_review_data() -> list[dict[str, Any]]:
    """Load augmented reviews produced in Module 1."""
    with REVIEW_DATA_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"Expected a list in {REVIEW_DATA_PATH}")
    return data


def _normalized(value: Any) -> str:
    return str(value or "").strip().casefold()


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


@mcp.resource("culinary-map://california")
def get_culinary_map() -> str:
    """Return the raw California Culinary Map restaurant descriptions."""
    return CULINARY_MAP_PATH.read_text(encoding="utf-8")


@mcp.tool()
def get_restaurant_info(restaurant_name: str) -> str:
    """Find restaurants by full or partial name and return their details."""
    query = _normalized(restaurant_name)
    if not query:
        return json.dumps(
            {"status": "invalid_query", "message": "Provide a restaurant name."},
            indent=2,
        )

    matches = []
    for restaurant in load_restaurant_data():
        name = _normalized(restaurant.get("name"))
        if name and (query in name or name in query):
            matches.append(restaurant)

    if not matches:
        return json.dumps(
            {
                "status": "not_found",
                "message": f"No restaurant found matching '{restaurant_name}'.",
                "suggestion": "Try a partial restaurant name.",
            },
            indent=2,
            ensure_ascii=False,
        )

    return json.dumps(
        {"status": "found", "count": len(matches), "results": matches},
        indent=2,
        ensure_ascii=False,
    )


@mcp.tool()
def recommend_by_vibe(vibe: str) -> str:
    """Search restaurant vibe and description fields plus the raw map text."""
    query = _normalized(vibe)
    if not query:
        return json.dumps(
            {"status": "invalid_query", "message": "Provide a vibe to search for."},
            indent=2,
        )

    structured_matches = []
    for restaurant in load_restaurant_data():
        searchable_values = (
            _as_string_list(restaurant.get("vibe"))
            + _as_string_list(restaurant.get("vibes"))
            + _as_string_list(restaurant.get("environment"))
            + _as_string_list(restaurant.get("description"))
        )
        if any(query in _normalized(value) for value in searchable_values):
            structured_matches.append(restaurant)

    raw_text = CULINARY_MAP_PATH.read_text(encoding="utf-8")
    text_excerpts = [
        paragraph.strip()[:500]
        for paragraph in raw_text.split("\n\n")
        if paragraph.strip() and query in paragraph.casefold()
    ][:5]

    return json.dumps(
        {
            "vibe_searched": vibe,
            "structured_matches": structured_matches,
            "raw_text_excerpts": text_excerpts,
        },
        indent=2,
        ensure_ascii=False,
    )


def _review_for_output(
    review: dict[str, Any], restaurant: dict[str, Any]
) -> dict[str, Any]:
    """Normalize the lab and project review field names for tool output."""
    image_description = review.get("image_description")
    if image_description is None:
        captions = review.get("image_captions")
        image_description = "; ".join(_as_string_list(captions)) if captions else "N/A"

    return {
        "restaurant": restaurant.get("name") or review.get("restaurant_name", "Unknown"),
        "reviewer": review.get("reviewer") or review.get("userId", "Unknown"),
        "rating": review.get("rating", "N/A"),
        "review_text": review.get("review_text") or review.get("text", ""),
        "image_description": image_description,
        "visit_date": review.get("visit_date") or review.get("date", "N/A"),
        "title": review.get("title", ""),
        "images": review.get("images", []),
    }


@mcp.tool()
def get_review(restaurant_name: str) -> str:
    """Return reviews for a restaurant, joining project reviews by itemId."""
    query = _normalized(restaurant_name)
    if not query:
        return json.dumps(
            {"status": "invalid_query", "message": "Provide a restaurant name."},
            indent=2,
        )

    restaurants = load_restaurant_data()
    restaurant_matches = [
        restaurant
        for restaurant in restaurants
        if (name := _normalized(restaurant.get("name")))
        and (query in name or name in query)
    ]

    if not restaurant_matches:
        return json.dumps(
            {
                "status": "not_found",
                "message": f"No restaurant found matching '{restaurant_name}'.",
            },
            indent=2,
            ensure_ascii=False,
        )

    restaurant_by_id = {
        str(restaurant.get("itemId")): restaurant
        for restaurant in restaurant_matches
        if restaurant.get("itemId") is not None
    }
    names = {_normalized(restaurant.get("name")) for restaurant in restaurant_matches}
    matching_reviews = []
    for review in load_review_data():
        linked_restaurant = restaurant_by_id.get(str(review.get("itemId")))
        direct_name = _normalized(review.get("restaurant_name"))
        if linked_restaurant is not None:
            matching_reviews.append(_review_for_output(review, linked_restaurant))
        elif direct_name and any(name and (direct_name == name) for name in names):
            matching_reviews.append(_review_for_output(review, {"name": direct_name}))

    if not matching_reviews:
        return json.dumps(
            {
                "status": "not_found",
                "message": f"No review found for '{restaurant_name}'.",
            },
            indent=2,
            ensure_ascii=False,
        )

    restaurant_label = matching_reviews[0]["restaurant"]
    return json.dumps(
        {
            "status": "found",
            "restaurant": restaurant_label,
            "count": len(matching_reviews),
            "results": matching_reviews,
        },
        indent=2,
        ensure_ascii=False,
    )


def _vector_response(operation_name: str, **kwargs) -> str:
    """Run a vector query and return a consistent, actionable MCP response."""
    try:
        vector_search = importlib.import_module("vector_search")
        operation = getattr(vector_search, operation_name)

        results = operation(**kwargs)
        return json.dumps(
            {"status": "ok", "index": str(vector_search.DB_DIR), "results": results},
            ensure_ascii=False,
        )
    except (RuntimeError, ImportError) as error:
        return json.dumps(
            {"status": "index_unavailable", "message": str(error)},
            ensure_ascii=False,
        )
    except Exception as error:
        return json.dumps(
            {"status": "search_failed", "message": str(error)},
            ensure_ascii=False,
        )


@mcp.tool()
def search_restaurants_semantic(
    query: str, limit: int = 5, cuisine: str = "", location: str = ""
) -> str:
    """Find restaurants by semantic meaning, with optional cuisine/location filters."""
    return _vector_response(
        "search_restaurants",
        query=query,
        limit=min(max(int(limit), 1), 20),
        cuisine=cuisine or None,
        location=location or None,
    )


@mcp.tool()
def search_recipes_semantic(query: str, limit: int = 5, cuisine: str = "") -> str:
    """Find recipes semantically by dish, ingredients, cuisine, or cooking method."""
    return _vector_response(
        "search_recipes",
        query=query,
        limit=min(max(int(limit), 1), 20),
        cuisine=cuisine or None,
    )


@mcp.tool()
def search_food_images_by_text(query: str, limit: int = 5, cuisine: str = "") -> str:
    """Find recipe images from a text description using CLIP embeddings."""
    return _vector_response(
        "search_food_images",
        query=query,
        limit=min(max(int(limit), 1), 20),
        cuisine=cuisine or None,
    )


@mcp.tool()
def search_food_images_by_image(image_path: str, limit: int = 5) -> str:
    """Find visually similar food images from a local indexed recipe image."""
    return _vector_response(
        "search_images_by_image",
        image_path=image_path,
        limit=min(max(int(limit), 1), 20),
    )


@mcp.tool()
def search_multimodal(
    query: str, limit: int = 5, cuisine: str = "", location: str = ""
) -> str:
    """Search restaurant, recipe, and food-image collections independently."""
    return _vector_response(
        "search_all",
        query=query,
        limit=min(max(int(limit), 1), 20),
        cuisine=cuisine or None,
        location=location or None,
    )


@mcp.tool()
def search_multimodal_fused(
    query: str,
    limit: int = 5,
    cuisine: str = "",
    location: str = "",
    text_weight: float = 0.6,
    image_weight: float = 0.4,
) -> str:
    """Fuse restaurant-text and food-image results after separate score normalization."""
    return _vector_response(
        "fuse_restaurants_and_images",
        query=query,
        limit=min(max(int(limit), 1), 20),
        cuisine=cuisine or None,
        location=location or None,
        text_weight=text_weight,
        image_weight=image_weight,
    )


if __name__ == "__main__":
    mcp.run()
