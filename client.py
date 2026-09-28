"""MCP client for the Connoisseur restaurant recommendation server."""

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import (
    CreateMessageRequestParams,
    CreateMessageResult,
    Root,
    TextContent,
)
from llm_config import CHAT_MODEL_ID, make_ollama_client


PROJECT_DIR = Path(__file__).resolve().parent
SERVER_SCRIPT = PROJECT_DIR / "server.py"
SERVER_PARAMS = StdioServerParameters(
    command=sys.executable,
    args=[str(SERVER_SCRIPT)],
)
def list_roots() -> list[Root]:
    """Declare the project directory as the client's shared filesystem root."""
    return [Root(uri=PROJECT_DIR.as_uri(), name=PROJECT_DIR.name)]


async def handle_sampling(
    params: CreateMessageRequestParams,
) -> CreateMessageResult:
    """Handle an LLM sampling request delegated by the MCP server."""
    if not params.messages:
        raise ValueError("Sampling request did not include a prompt message.")

    messages = []
    if params.systemPrompt:
        messages.append({"role": "system", "content": params.systemPrompt})
    for message in params.messages:
        content = message.content
        text = content if isinstance(content, str) else getattr(content, "text", None)
        if text:
            role = getattr(message.role, "value", message.role)
            messages.append({"role": role, "content": text})
    if not messages:
        raise ValueError("Sampling request did not contain a text prompt.")

    print("\n[Sampling] Server requested an LLM task:")
    print(f"  Prompt preview: {messages[-1]['content'][:150]}...")

    response = make_ollama_client().chat.completions.create(
        model=CHAT_MODEL_ID,
        max_tokens=params.maxTokens or 200,
        messages=messages,
    )
    response_text = response.choices[0].message.content or ""
    if not response_text:
        raise RuntimeError("Ollama returned no text for the sampling request.")

    print(f"  LLM response: {response_text[:100]}...")
    return CreateMessageResult(
        role="assistant",
        content=TextContent(type="text", text=response_text),
        model=CHAT_MODEL_ID,
    )


@asynccontextmanager
async def _connected_session():
    async with stdio_client(SERVER_PARAMS) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            sampling_callback=handle_sampling,
            list_roots_callback=list_roots,
        ) as session:
            await session.initialize()
            yield session


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    """Call one server tool and decode its JSON text response."""
    async with _connected_session() as session:
        result = await session.call_tool(tool_name, arguments=arguments)

    if result.isError:
        details = "\n".join(
            block.text for block in result.content if isinstance(block, TextContent)
        )
        raise RuntimeError(f"MCP tool {tool_name!r} failed: {details}")

    text_blocks = [
        block.text for block in result.content if isinstance(block, TextContent)
    ]
    if not text_blocks:
        raise RuntimeError(f"MCP tool {tool_name!r} returned no text content.")
    return json.loads(text_blocks[0])


async def verify_connection() -> None:
    """Verify server startup, tool/resource discovery, and configured roots."""
    print("=" * 60)
    print("MCP Connection Verification")
    print("=" * 60)

    async with _connected_session() as session:
        tools_result = await session.list_tools()
        tool_names = {tool.name for tool in tools_result.tools}
        required_tools = {
            "get_restaurant_info",
            "recommend_by_vibe",
            "get_review",
        }

        print("--- START SCREENSHOT ---")
        print(f"\nDiscovered {len(tools_result.tools)} tools:")
        for tool in tools_result.tools:
            print(f"  - {tool.name}: {tool.description[:80]}...")

        missing_tools = required_tools - tool_names
        if missing_tools:
            raise RuntimeError(f"MCP server is missing tools: {sorted(missing_tools)}")
        print("\nAll required tools verified!")

        resources_result = await session.list_resources()
        print(f"\nDiscovered {len(resources_result.resources)} resources:")
        for resource in resources_result.resources:
            print(f"  - {resource.uri}: {resource.name}")

        roots = list_roots()
        print(f"\nConfigured {len(roots)} roots:")
        for root in roots:
            print(f"  - {root.name}: {root.uri}")
        print("--- END SCREENSHOT ---")


async def demo_get_restaurant_info() -> None:
    print("\n" + "-" * 60)
    print("Demo: get_restaurant_info('Iron & Embers')")
    print("-" * 60)
    data = await call_tool(
        "get_restaurant_info",
        {"restaurant_name": "Iron & Embers"},
    )
    print(json.dumps(data, indent=2, ensure_ascii=False))


async def demo_recommend_by_vibe() -> None:
    print("\n" + "-" * 60)
    print("Demo: recommend_by_vibe('moody')")
    print("-" * 60)
    data = await call_tool("recommend_by_vibe", {"vibe": "moody"})
    print(f"Vibe: {data['vibe_searched']}")
    print(f"Structured matches: {len(data['structured_matches'])}")
    for match in data["structured_matches"]:
        cuisine = match.get("food_style") or match.get("cuisine", "N/A")
        print(f"  - {match.get('name', 'Unknown')} ({cuisine}) - {match.get('rating', 'N/A')}/5")
    print(f"Raw text excerpts: {len(data['raw_text_excerpts'])}")


async def demo_get_review() -> None:
    print("\n" + "-" * 60)
    print("Demo: get_review('Iron & Embers')")
    print("-" * 60)
    data = await call_tool("get_review", {"restaurant_name": "Iron & Embers"})
    print(json.dumps(data, indent=2, ensure_ascii=False))


async def main() -> None:
    """Run the three tool demos and then verify discovery and roots."""
    await demo_get_restaurant_info()
    await demo_recommend_by_vibe()
    await demo_get_review()
    await verify_connection()


if __name__ == "__main__":
    asyncio.run(main())
