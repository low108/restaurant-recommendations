"""Create the local Chroma collections used by the MCP semantic-search tools."""

import json
import os
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent
os.environ["HF_HOME"] = str(DATA_DIR / "model_cache")
os.environ["HF_HUB_CACHE"] = str(DATA_DIR / "model_cache" / "hub")

import chromadb
import numpy as np
import torch
from PIL import Image
from sentence_transformers import SentenceTransformer
from transformers import CLIPModel, CLIPProcessor


DB_DIR = DATA_DIR / "chroma_multimodal"
RESTAURANT_FILE = DATA_DIR / "structured_restaurant_data.json"
RECIPE_FILE = DATA_DIR / "augmented_food_recipe.json"
IMAGE_DIR = DATA_DIR / "synthetic_recipe_images"
TEXT_MODEL_NAME = "all-MiniLM-L6-v2"
CLIP_MODEL_NAME = "openai/clip-vit-base-patch32"


def _clip_embedding(output):
    """Handle Transformers CLIP outputs across tuple and ModelOutput releases."""
    if hasattr(output, "pooler_output"):
        return output.pooler_output
    if isinstance(output, tuple):
        return output[0]
    return output


def load_records(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        records = json.load(file)
    if not isinstance(records, list):
        raise ValueError(f"Expected a JSON list in {path}")
    return records


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def _metadata(values: dict[str, Any]) -> dict[str, str | int | float | bool]:
    clean = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            clean[key] = value
        elif isinstance(value, (list, tuple)):
            clean[key] = _text(value)
        else:
            clean[key] = str(value)
    return clean


def restaurant_document(record: dict[str, Any]) -> str:
    fields = (
        ("Restaurant", record.get("name")),
        ("Cuisine", record.get("food_style") or record.get("cuisine")),
        ("Type", record.get("type")),
        ("Location", record.get("location")),
        ("Vibe", record.get("vibe") or record.get("vibes")),
        ("Environment", record.get("environment")),
        ("Signature dishes", record.get("signatures") or record.get("signature_dish")),
        ("Description", record.get("description")),
        ("Rating", record.get("rating")),
        ("Price range", record.get("price_range")),
        ("Shortcomings", record.get("shortcomings")),
    )
    return "\n".join(f"{label}: {_text(value)}" for label, value in fields if _text(value))


def recipe_document(record: dict[str, Any]) -> str:
    fields = (
        ("Recipe", record.get("name")),
        ("Cuisine", record.get("cuisine")),
        ("Ingredients", record.get("ingredients")),
        ("Directions", record.get("directions")),
        ("Prep time", record.get("prep_time")),
        ("Cook time", record.get("cook_time")),
        ("Total time", record.get("total_time")),
        ("Image description", record.get("image_description")),
    )
    return "\n".join(f"{label}: {_text(value)}" for label, value in fields if _text(value))


def replace_collection(
    client,
    name: str,
    ids: list[str],
    documents: list[str],
    metadatas: list[dict[str, Any]],
    embeddings: np.ndarray,
) -> None:
    existing = {getattr(item, "name", item) for item in client.list_collections()}
    if name in existing:
        client.delete_collection(name)
    collection = client.create_collection(name, metadata={"hnsw:space": "cosine"})
    if ids:
        collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings.tolist(),
        )


def _embed_recipe_images(records: list[dict[str, Any]], model, processor):
    available = []
    for index, record in enumerate(records):
        recipe_id = record.get("id", index + 1)
        image_path = IMAGE_DIR / f"recipe{recipe_id}.png"
        if image_path.is_file():
            available.append((index, record, image_path))
        else:
            print(f"Skipping missing image: {image_path.name}")

    vectors = []
    batch_size = 16
    for start in range(0, len(available), batch_size):
        batch = available[start : start + batch_size]
        images = []
        for _, _, path in batch:
            with Image.open(path) as image:
                images.append(image.convert("RGB"))
        inputs = processor(images=images, return_tensors="pt")
        with torch.no_grad():
            features = _clip_embedding(model.get_image_features(**inputs))
            features = features / features.norm(dim=-1, keepdim=True)
        vectors.append(features.cpu().numpy().astype(np.float32))
        print(f"Embedded images: {min(start + batch_size, len(available))}/{len(available)}")

    matrix = np.vstack(vectors) if vectors else np.empty((0, 512), dtype=np.float32)
    return available, matrix


def main() -> None:
    if not RESTAURANT_FILE.is_file():
        raise FileNotFoundError(f"Restaurant data not found: {RESTAURANT_FILE}")
    if not RECIPE_FILE.is_file():
        raise FileNotFoundError(f"Recipe data not found: {RECIPE_FILE}")
    if not IMAGE_DIR.is_dir():
        raise FileNotFoundError(f"Recipe image folder not found: {IMAGE_DIR}")

    restaurants = load_records(RESTAURANT_FILE)
    recipes = load_records(RECIPE_FILE)
    print(f"Loaded {len(restaurants)} restaurants and {len(recipes)} recipes.")

    print(f"Loading text model: {TEXT_MODEL_NAME}")
    text_model = SentenceTransformer(TEXT_MODEL_NAME, device="cpu")

    restaurant_records = [record for record in restaurants if record.get("name")]
    restaurant_ids = [
        f"rest_{record.get('itemId', index)}"
        for index, record in enumerate(restaurant_records)
    ]
    restaurant_docs = [restaurant_document(record) for record in restaurant_records]
    restaurant_metadata = [
        _metadata(
            {
                "doc_id": restaurant_ids[index],
                "source": "restaurant",
                "itemId": record.get("itemId"),
                "name": record.get("name"),
                "cuisine": record.get("food_style") or record.get("cuisine"),
                "location": record.get("location"),
                "rating": record.get("rating"),
                "price_range": record.get("price_range"),
                "vibe": record.get("vibe") or record.get("vibes"),
            }
        )
        for index, record in enumerate(restaurant_records)
    ]
    restaurant_vectors = text_model.encode(
        restaurant_docs,
        batch_size=64,
        show_progress_bar=True,
        normalize_embeddings=True,
    ).astype(np.float32)

    recipe_ids = [f"recipe_{record.get('id', index + 1)}" for index, record in enumerate(recipes)]
    recipe_docs = [recipe_document(record) for record in recipes]
    recipe_metadata = [
        _metadata(
            {
                "doc_id": recipe_ids[index],
                "source": "recipe",
                "recipe_id": record.get("id", index + 1),
                "name": record.get("name"),
                "cuisine": record.get("cuisine"),
                "prep_time": record.get("prep_time"),
                "total_time": record.get("total_time"),
            }
        )
        for index, record in enumerate(recipes)
    ]
    recipe_vectors = text_model.encode(
        recipe_docs,
        batch_size=64,
        show_progress_bar=True,
        normalize_embeddings=True,
    ).astype(np.float32)

    print(f"Loading image model: {CLIP_MODEL_NAME}")
    clip_model = CLIPModel.from_pretrained(CLIP_MODEL_NAME).to("cpu")
    clip_processor = CLIPProcessor.from_pretrained(CLIP_MODEL_NAME, use_fast=True)
    clip_model.eval()
    image_records, image_vectors = _embed_recipe_images(recipes, clip_model, clip_processor)
    image_ids = [f"recipe_img_{record.get('id', index + 1)}" for index, (_, record, _) in enumerate(image_records)]
    image_docs = [
        " ".join(
            part for part in (
                _text(record.get("name")),
                _text(record.get("cuisine")),
                _text(record.get("image_description")),
            ) if part
        )
        for _, record, _ in image_records
    ]
    image_metadata = [
        _metadata(
            {
                "doc_id": image_ids[index],
                "source": "recipe_image",
                "recipe_id": record.get("id", recipe_index + 1),
                "name": record.get("name"),
                "cuisine": record.get("cuisine"),
                "image_path": str(image_path.resolve()),
            }
        )
        for index, (recipe_index, record, image_path) in enumerate(image_records)
    ]

    DB_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(DB_DIR))
    replace_collection(
        client, "restaurant_articles", restaurant_ids, restaurant_docs,
        restaurant_metadata, restaurant_vectors,
    )
    replace_collection(
        client, "recipe_text", recipe_ids, recipe_docs,
        recipe_metadata, recipe_vectors,
    )
    replace_collection(
        client, "food_images", image_ids, image_docs,
        image_metadata, image_vectors,
    )

    print(f"Index saved to {DB_DIR}")
    print(f"Indexed restaurants: {len(restaurant_ids)}")
    print(f"Indexed recipes: {len(recipe_ids)}")
    print(f"Indexed recipe images: {len(image_ids)}")


if __name__ == "__main__":
    main()
