"""Normal tests never inherit an operator's live model configuration."""

import os

import pytest


@pytest.fixture(autouse=True)
def isolated_inference_environment(monkeypatch):
    def relevant(name):
        return name.startswith("DINING_LLM_") or name in {
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OLLAMA_MODEL",
            "OLLAMA_BASE_URL",
        }

    previous = {name: value for name, value in os.environ.items() if relevant(name)}
    for name in previous:
        monkeypatch.delenv(name)
    try:
        yield
    finally:
        # dotenv can introduce keys outside monkeypatch. Undo test patches first,
        # then restore the complete relevant environment, including absent keys.
        monkeypatch.undo()
        for name in list(os.environ):
            if relevant(name):
                os.environ.pop(name)
        os.environ.update(previous)
