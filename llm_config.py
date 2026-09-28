"""Shared local Ollama configuration for the app and data helpers."""

import os


OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
OLLAMA_API_KEY = "ollama"  # Ollama's OpenAI-compatible API ignores this placeholder.
CHAT_MODEL_ID = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
VISION_MODEL_ID = os.environ.get("OLLAMA_VISION_MODEL", "qwen3.5:4b")


def make_ollama_client():
    """Create an OpenAI SDK client pointed at the local Ollama server."""
    from openai import OpenAI

    return OpenAI(base_url=OLLAMA_BASE_URL, api_key=OLLAMA_API_KEY)
