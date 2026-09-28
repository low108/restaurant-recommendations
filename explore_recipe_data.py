"""Explore recipe JSON metadata and display the first recipe image."""

import json
import base64
import mimetypes
import sys
from pathlib import Path
from llm_config import VISION_MODEL_ID, make_ollama_client

PROJECT_DIR = Path(__file__).resolve().parent
IMAGE_PATH = PROJECT_DIR / "synthetic_recipe_images" / "recipe1.png"


def vision_llm(system_msg, prompt_txt, image_path):
    """Send a text prompt and local image to the configured local vision model."""

    # Step 2.2: Encode the image as base64.
    image_path = Path(image_path)
    with image_path.open("rb") as image_file:
        encoded_image = base64.b64encode(image_file.read()).decode("utf-8")
    image_mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"

    # Step 2.3: Send text and image using OpenAI-compatible chat content blocks.
    response = make_ollama_client().chat.completions.create(
        model=VISION_MODEL_ID,
        max_tokens=300,
        messages=[
            {"role": "system", "content": system_msg},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_txt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image_mime_type};base64,{encoded_image}"
                        },
                    },
                ],
            },
        ],
    )
    return response.choices[0].message.content or ""


def image_caption_prompt_template(food_name):
    """Create prompts for a concise, image-grounded food caption."""
    image_caption_system_msg = (
        "You are a careful food image captioning assistant. Describe only "
        "visible details in the image. Do not infer hidden ingredients, "
        "preparation steps, or other facts that cannot be seen. Be concise."
    )
    image_caption_prompt_txt = (
        f"Describe the food image for the recipe named '{food_name}'. "
        "Focus on visible ingredients, cooking style, presentation, and "
        "portion size. If a detail is uncertain, leave it out. Return only "
        "the caption."
    )
    return image_caption_system_msg, image_caption_prompt_txt


def caption_all_recipes(recipe_data):
    """Generate and attach an image caption to each recipe record."""
    for i in range(len(recipe_data)):
        recipe = recipe_data[i]
        recipe_id = recipe.get("id", i + 1)
        image_path = PROJECT_DIR / "synthetic_recipe_images" / f"recipe{recipe_id}.png"

        if (i + 1) % 20 == 0:
            print(f"{i + 1} out of {len(recipe_data)} is done")

        # Step 4.1: Get caption prompts for this recipe's food name.
        system_msg, prompt_txt = image_caption_prompt_template(recipe["name"])

        # Step 4.2: Generate a caption from the matching image.
        response = vision_llm(system_msg, prompt_txt, image_path)

        # Add the caption to this recipe record.
        recipe["image_description"] = response

    print("ALL DONE!")
    return recipe_data


def review_context_image_caption_prompt_template(reviews):
    """Create prompts for captioning a food image with review context."""
    review_context_image_caption_system_msg = (
        "You are a careful culinary image captioning assistant. Describe the "
        "food and presentation visible in the image. Use the review only as "
        "context for what to pay attention to; do not treat it as proof of "
        "details that are not visible. Do not invent ingredients or visual "
        "features. Keep the caption concise."
    )
    review_context_image_caption_prompt_txt = (
        "Write a concise caption for the attached food image. Focus on the "
        "visible dish, ingredients, preparation cues, and presentation. Use "
        "this customer review as context, while grounding the caption in the "
        f"image:\n\n{reviews}"
    )
    return (
        review_context_image_caption_system_msg,
        review_context_image_caption_prompt_txt,
    )


def caption_review_images(review_text, image_urls):
    """Download and caption every image associated with one review."""
    import requests
    from urllib.parse import urlparse

    system_msg, prompt_txt = review_context_image_caption_prompt_template(review_text)
    captions = []

    for index, image_url in enumerate(image_urls, start=1):
        image_response = requests.get(image_url, timeout=30)
        image_response.raise_for_status()

        suffix = Path(urlparse(image_url).path).suffix or ".png"
        image_path = PROJECT_DIR / f"review_image_placeholder_{index}{suffix}"
        image_path.write_bytes(image_response.content)

        captions.append(vision_llm(system_msg, prompt_txt, image_path))

    return captions


def load_recipe_data(json_path=None):
    """Load recipe JSON from a supplied path or the sole project JSON file."""
    if json_path is None:
        candidates = sorted(PROJECT_DIR.glob("*.json"))
        if len(candidates) != 1:
            names = ", ".join(path.name for path in candidates) or "none found"
            raise FileNotFoundError(
                "Specify the recipe JSON path. Project JSON files: " + names
            )
        json_path = candidates[0]

    with Path(json_path).open("r", encoding="utf-8") as file:
        return json.load(file)


def get_first_recipe(recipe_data):
    """Return the first recipe from common list or object JSON layouts."""
    if isinstance(recipe_data, list):
        if not recipe_data:
            raise ValueError("The recipe JSON list is empty.")
        return recipe_data[0]

    if isinstance(recipe_data, dict):
        recipes = recipe_data.get("recipes")
        if isinstance(recipes, list):
            if not recipes:
                raise ValueError("The 'recipes' list is empty.")
            return recipes[0]
        return recipe_data

    raise TypeError("Expected the recipe JSON to contain an object or list.")


def explore_recipe_data(json_path=None):
    # Step 1.1: Load JSON data.
    recipe_data = load_recipe_data(json_path)

    # Step 1.2: Print the first recipe's fields, value types, and values.
    first_recipe = get_first_recipe(recipe_data)
    for key, value in first_recipe.items():
        print(f"{key} ({type(value).__name__}): {value}")

    # Step 1.3: Display the image associated with recipe1.
    if not IMAGE_PATH.is_file():
        raise FileNotFoundError(f"Recipe image not found: {IMAGE_PATH}")

    try:
        from IPython.display import Image, display

        display(Image(filename=str(IMAGE_PATH)))
    except ImportError:
        from PIL import Image

        Image.open(IMAGE_PATH).show()

    return recipe_data


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    recipe_data = explore_recipe_data(path)
