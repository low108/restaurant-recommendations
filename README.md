# Connoisseur Companion

A local restaurant recommendation app built with Gradio, LangGraph, an MCP server, Chroma, and Ollama. Six separately configured agents update a dining profile, retrieve candidates, analyze food trends, styles, and dietary information, then assemble recommendations. The MCP server provides restaurant lookup, reviews, and semantic restaurant, recipe, and food image search.

The included California restaurant, review, recipe, and synthetic image data comes from the course project. Recommendations are based on these static sample records, not live restaurant listings. Dietary information is informational and should be verified with the restaurant.

## Run locally

Prerequisites: Python 3.12 and [Ollama](https://ollama.com/).

```bash
ollama pull llama3.1:8b
python3.12 -m venv .venv312
source .venv312/bin/activate
python -m pip install -r requirements-app.txt
python build_vector_index.py
python app.py
```

Open `http://127.0.0.1:7860/`. Ollama must be running while the app is in use. To change the chat model, set `OLLAMA_MODEL`; to change its OpenAI-compatible endpoint, set `OLLAMA_BASE_URL`. Set `GRADIO_SERVER_PORT` to use another port.

The first index build downloads the MiniLM and CLIP embedding models. The vector index and model cache are generated locally and are excluded from Git. Rebuild the index after changing the data or moving the project to another computer, since indexed image paths point to local files.

## MCP tools

Run `python client.py` to connect to the MCP server over stdio and exercise its data tools. `server.py` exposes restaurant and review lookup, vibe search, semantic restaurant and recipe search, text-to-image and image-to-image search, plus multimodal retrieval. The Gradio app starts the MCP server as a local subprocess when handling a request.

## Project layout

- `app.py`: Gradio interface and local Ollama chat model.
- `workflow.py` and `agent_configs.py`: LangGraph state, agents, and orchestration.
- `server.py` and `vector_search.py`: MCP tools and Chroma retrieval.
- `build_vector_index.py`: MiniLM text and CLIP image embedding pipeline.
- `structured_restaurant_data.json`, `augmented_food_recipe.json`, `augmented_user_review.json`, and `synthetic_recipe_images/`: sample project data.

See [VECTOR_RETRIEVAL.md](VECTOR_RETRIEVAL.md) for the retrieval design and setup details.
