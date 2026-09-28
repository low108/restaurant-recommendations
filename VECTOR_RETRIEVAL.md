# Semantic and multimodal retrieval

## Setup

From the project directory, create and activate an environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

Install the app and vector dependencies in the same Python environment:

```bash
python -m pip install -r requirements-app.txt
```

Build the local Chroma index once:

```bash
.venv/bin/python build_vector_index.py
```

Make sure the Ollama app/server is running and the configured models are installed, then launch the interface:

```bash
.venv/bin/python app.py
```

The Gradio app and MCP sampling use `llama3.1:8b` through Ollama's local
OpenAI-compatible chat endpoint. The recipe image-caption helper uses
`qwen3.5:4b` for local vision; install it with `ollama pull qwen3.5:4b` before
running that helper.

The builder uses `all-MiniLM-L6-v2` for restaurant and recipe text and
`openai/clip-vit-base-patch32` for recipe images. The first run downloads the
models. It writes the three collections to `chroma_multimodal/` beside the
project files and caches model weights in `model_cache/` beside them.
Re-running the builder refreshes only those three collections.

Start the MCP server or the Gradio app as usual. The MCP server exposes:

- `search_restaurants_semantic`: restaurant descriptions, with optional cuisine
  and location filters.
- `search_recipes_semantic`: recipe text, with an optional cuisine filter.
- `search_food_images_by_text`: text-to-image retrieval in CLIP's shared space.
- `search_food_images_by_image`: image-to-image retrieval for a local recipe image.
- `search_multimodal`: separate ranked results for restaurants, recipes, and images.
- `search_multimodal_fused`: optional restaurant-text/image fusion with explicit
  weights and per-modality score normalization.

Text and image scores come from different embedding models, so the default
multimodal search keeps their rankings separate. The fused search helper
normalizes each modality's scores before applying weights.

## App request flow

`agent_configs.py` separately defines the six lab-style agents: profile
generator, RAG retriever, food trend analyst, food style expert, nutrition
expert, and recommendation expert. `workflow.py` defines the shared nine-field
`AgentState` and controls execution with LangGraph:

1. Generate/update the user profile and retain it in Gradio session state.
2. Retrieve real candidates by calling the local MCP search tools.
3. Run trend, food-style, and nutrition agents concurrently.
4. Join their state updates and synthesize recommendations.

The graph state carries `user_input`, `user_profile`, `retrieved_restaurants`,
`retrieved_recipes`, `trend_analysis`, `style_analysis`, `nutrition_analysis`,
`final_recommendations`, and `workflow_step`. Retrieved recipe images appear in
the gallery when available.

The profile is held in the browser session only; the app has a button to clear
it. Dietary checks only compare stated constraints with available recipe data
and do not provide medical advice.

Example requests:

- “I like spicy Korean food and want something under $25. Find dinner nearby.”
- “Show recipes with chickpeas and lemon, then find images that look similar to
  this dish.”
- “I’m vegetarian. Which of these recipes looks suitable, and what ingredients
  should I verify?”
