"""Gradio MCP host for the Connoisseur Companion restaurant assistant."""

import os
from typing import Any

import gradio as gr
from langchain_openai import ChatOpenAI
from llm_config import CHAT_MODEL_ID, OLLAMA_API_KEY, OLLAMA_BASE_URL
from workflow import PROFILE_DEFAULTS, run_workflow


def make_model() -> ChatOpenAI:
    """Create the local Ollama chat model for one conversation turn."""
    return ChatOpenAI(
        model=CHAT_MODEL_ID,
        base_url=OLLAMA_BASE_URL,
        api_key=OLLAMA_API_KEY,
        temperature=0.7,
    )


async def handle_chat(
    user_message: str,
    history: list[dict[str, Any]] | None,
    profile: dict[str, Any] | None,
):
    """Stream a thinking placeholder, then replace it with the agent response."""
    history = list(history or [])
    if not user_message or not user_message.strip():
        yield history, profile or {}, [], profile or {}
        return

    history.extend(
        [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": "Thinking…"},
        ]
    )
    yield history, profile or {}, [], profile or {}

    try:
        response_text, updated_profile, image_paths = await run_workflow(
            user_message, history[:-2], profile or {}, make_model
        )
    except Exception as error:
        response_text = f"I couldn't reach the recommendation service: {error}"
        updated_profile, image_paths = profile or {}, []
    history[-1] = {"role": "assistant", "content": response_text}
    yield history, updated_profile, image_paths, updated_profile


def clear_profile():
    return {key: value.copy() if isinstance(value, list) else value for key, value in PROFILE_DEFAULTS.items()}


with gr.Blocks(title="Connoisseur Companion", theme=gr.themes.Soft()) as demo:
    gr.Markdown(
        "# Connoisseur Companion\n"
        "Your AI guide to California's restaurant scene. Ask about a restaurant, "
        "reviews, or the atmosphere you’re looking for."
    )

    chatbot = gr.Chatbot(height=500, label="Conversation")
    profile_state = gr.State(clear_profile())
    profile_view = gr.JSON(label="Your session preferences", value=clear_profile())
    image_gallery = gr.Gallery(label="Recipe images from retrieval", columns=4, height="auto")
    msg_input = gr.Textbox(
        label="Ask about restaurants",
        placeholder='Try “Find a moody restaurant” or “Tell me about Iron & Embers”',
    )

    with gr.Row():
        btn1 = gr.Button("Find moody restaurants", size="sm")
        btn2 = gr.Button("Tell me about Iron & Embers", size="sm")
        btn3 = gr.Button("Zen dining in Little Tokyo?", size="sm")
        clear_profile_btn = gr.Button("Clear my preference profile", size="sm")

    submit_event = msg_input.submit(
        handle_chat,
        inputs=[msg_input, chatbot, profile_state],
        outputs=[chatbot, profile_state, image_gallery, profile_view],
    )
    submit_event.then(lambda: "", inputs=None, outputs=msg_input)

    btn1.click(
        handle_chat,
        inputs=[gr.State("Find me some moody restaurants"), chatbot, profile_state],
        outputs=[chatbot, profile_state, image_gallery, profile_view],
    )
    btn2.click(
        handle_chat,
        inputs=[gr.State("Tell me about Iron & Embers"), chatbot, profile_state],
        outputs=[chatbot, profile_state, image_gallery, profile_view],
    )
    btn3.click(
        handle_chat,
        inputs=[gr.State("What's a zen dining experience in Little Tokyo?"), chatbot, profile_state],
        outputs=[chatbot, profile_state, image_gallery, profile_view],
    )

    clear_profile_btn.click(
        lambda: (clear_profile(), clear_profile()),
        inputs=None,
        outputs=[profile_state, profile_view],
    )

    gr.Markdown(
        "*The local MCP server provides semantic restaurant, recipe, and image retrieval. "
        "Preference profiles are stored in this browser session and can be cleared at any time.*"
    )


if __name__ == "__main__":
    print(f"Starting Connoisseur Companion with local Ollama model {CHAT_MODEL_ID}...")
    demo.queue().launch(
        server_port=int(os.environ.get("GRADIO_SERVER_PORT", "7860")),
        share=False,
    )
