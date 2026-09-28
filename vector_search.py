"""Embedding encoders and Chroma queries used by the MCP vector tools."""

from functools import lru_cache
import os
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent
os.environ["HF_HOME"] = str(DATA_DIR / "model_cache")
os.environ["HF_HUB_CACHE"] = str(DATA_DIR / "model_cache" / "hub")
DB_DIR = DATA_DIR / "chroma_multimodal"
RESTAURANT_COLLECTION = "restaurant_articles"
RECIPE_COLLECTION = "recipe_text"
IMAGE_COLLECTION = "food_images"


def _require_index() -> None:
    if not DB_DIR.is_dir():
        raise RuntimeError(
            f"Vector index not found at {DB_DIR}. Build it with "
            "`python build_vector_index.py` first."
        )


@lru_cache(maxsize=1)
def _client():
    _require_index()
    import chromadb

    return chromadb.PersistentClient(path=str(DB_DIR))


@lru_cache(maxsize=3)
def _collection(name: str):
    client = _client()
    try:
        return client.get_collection(name)
    except Exception as error:
        raise RuntimeError(
            f"Vector collection {name!r} is unavailable. Run "
            "`python build_vector_index.py` to build the index."
        ) from error


@lru_cache(maxsize=1)
def _text_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer("all-MiniLM-L6-v2", device="cpu")


@lru_cache(maxsize=1)
def _clip_models():
    import torch
    from transformers import CLIPModel, CLIPProcessor

    name = "openai/clip-vit-base-patch32"
    model = CLIPModel.from_pretrained(name).to("cpu")
    processor = CLIPProcessor.from_pretrained(name, use_fast=True)
    model.eval()
    return torch, model, processor


def _clip_embedding(output):
    """Handle Transformers CLIP outputs across tuple and ModelOutput releases."""
    if hasattr(output, "pooler_output"):
        return output.pooler_output
    if isinstance(output, tuple):
        return output[0]
    return output


def _where_filter(cuisine: str | None = None, location: str | None = None):
    filters = []
    if cuisine and cuisine.strip():
        filters.append({"cuisine": {"$eq": cuisine.strip()}})
    if location and location.strip():
        filters.append({"location": {"$eq": location.strip()}})
    if len(filters) == 1:
        return filters[0]
    if filters:
        return {"$and": filters}
    return None


def _query(collection_name: str, vector, limit: int, where=None):
    collection = _collection(collection_name)
    count = collection.count()
    if count == 0:
        return []

    result = collection.query(
        query_embeddings=[vector.tolist()],
        n_results=min(max(1, int(limit)), count),
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    ids = result.get("ids", [[]])[0]
    texts = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    matches = []
    for index, result_id in enumerate(ids):
        distance = float(distances[index])
        matches.append(
            {
                "id": result_id,
                "text": texts[index] if index < len(texts) else "",
                "metadata": metadatas[index] if index < len(metadatas) else {},
                "distance": distance,
                "similarity": 1.0 - distance,
            }
        )
    return matches


def search_restaurants(
    query: str,
    limit: int = 5,
    cuisine: str | None = None,
    location: str | None = None,
) -> list[dict[str, Any]]:
    """Search indexed restaurant text using MiniLM query embeddings."""
    vector = _text_model().encode(
        query,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return _query(
        RESTAURANT_COLLECTION,
        vector,
        limit,
        _where_filter(cuisine=cuisine, location=location),
    )


def search_recipes(
    query: str,
    limit: int = 5,
    cuisine: str | None = None,
) -> list[dict[str, Any]]:
    """Search indexed recipe text (ingredients, directions, and metadata)."""
    vector = _text_model().encode(
        query,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return _query(
        RECIPE_COLLECTION,
        vector,
        limit,
        _where_filter(cuisine=cuisine),
    )


def search_food_images(
    query: str,
    limit: int = 5,
    cuisine: str | None = None,
) -> list[dict[str, Any]]:
    """Search recipe images from text in CLIP's shared embedding space."""
    torch, model, processor = _clip_models()
    inputs = processor(text=[query], return_tensors="pt", padding=True)
    with torch.no_grad():
        features = _clip_embedding(model.get_text_features(**inputs))
        features = features / features.norm(dim=-1, keepdim=True)
    vector = features[0].cpu().numpy()
    return _query(
        IMAGE_COLLECTION,
        vector,
        limit,
        _where_filter(cuisine=cuisine),
    )


def search_images_by_image(
    image_path: str,
    limit: int = 5,
    cuisine: str | None = None,
) -> list[dict[str, Any]]:
    """Find visually similar indexed images using a local image query."""
    from PIL import Image

    torch, model, processor = _clip_models()
    image_file = Path(image_path).resolve()
    image_root = (DATA_DIR / "synthetic_recipe_images").resolve()
    if image_root not in image_file.parents or not image_file.is_file():
        raise ValueError("image_path must point to an existing project recipe image.")

    image = Image.open(image_file).convert("RGB")
    inputs = processor(images=[image], return_tensors="pt")
    with torch.no_grad():
        features = _clip_embedding(model.get_image_features(**inputs))
        features = features / features.norm(dim=-1, keepdim=True)
    vector = features[0].cpu().numpy()
    return _query(
        IMAGE_COLLECTION,
        vector,
        limit,
        _where_filter(cuisine=cuisine),
    )


def search_all(
    query: str,
    limit: int = 5,
    cuisine: str | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    """Return separately ranked text and image results for one query.

    Similarity values from MiniLM and CLIP are kept within their own modality;
    their raw scores are not compared or blended.
    """
    return {
        "restaurants": search_restaurants(query, limit, cuisine, location),
        "recipes": search_recipes(query, limit, cuisine),
        "food_images": search_food_images(query, limit, cuisine),
    }


def fuse_restaurants_and_images(
    query: str,
    limit: int = 5,
    cuisine: str | None = None,
    location: str | None = None,
    text_weight: float = 0.6,
    image_weight: float = 0.4,
) -> list[dict[str, Any]]:
    """Fuse article and image ranks using per-modality min-max scores."""
    if text_weight < 0 or image_weight < 0 or text_weight + image_weight == 0:
        raise ValueError("Weights must be non-negative and have a positive sum.")
    weight_sum = text_weight + image_weight
    text_weight /= weight_sum
    image_weight /= weight_sum

    articles = search_restaurants(query, limit, cuisine, location)
    images = search_food_images(query, limit, cuisine)

    def normalize(matches):
        if not matches:
            return []
        values = [float(match["similarity"]) for match in matches]
        low, high = min(values), max(values)
        if high - low < 1e-8:
            return [1.0] * len(values)
        return [(value - low) / (high - low) for value in values]

    article_scores = normalize(articles)
    image_scores = normalize(images)
    fused = []
    for match, score in zip(articles, article_scores):
        fused.append(
            {
                "modality": "restaurant",
                "id": match["id"],
                "metadata": match["metadata"],
                "text": match["text"],
                "text_score": score,
                "image_score": 0.0,
                "fused_score": text_weight * score,
            }
        )
    for match, score in zip(images, image_scores):
        fused.append(
            {
                "modality": "image",
                "id": match["id"],
                "metadata": match["metadata"],
                "text": match["text"],
                "text_score": 0.0,
                "image_score": score,
                "fused_score": image_weight * score,
            }
        )
    fused.sort(key=lambda item: item["fused_score"], reverse=True)
    return fused[: max(1, min(int(limit), len(fused)))]
