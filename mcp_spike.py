"""YouCam MCP 接続スパイク（ADK MCPToolset → Streamable HTTP + Bearer）.

使い方:
    .venv/bin/python mcp_spike.py            # ツール列挙（demo鯖はキー無しでも通る）
    YOUCAM_API_KEY=xxxx .venv/bin/python mcp_spike.py   # Bearer 付きで列挙
"""
import asyncio
import os

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

MCP_URL = "https://mcp-api-01.makeupar.com/mcp"


async def main() -> None:
    api_key = os.environ.get("YOUCAM_API_KEY", "")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    print(f"endpoint  : {MCP_URL}")
    print(f"bearer    : {'set (' + str(len(api_key)) + ' chars)' if api_key else 'NONE'}")

    toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(url=MCP_URL, headers=headers),
    )
    try:
        tools = await toolset.get_tools()
        print(f"connected : OK — {len(tools)} tools")
        for t in tools[:8]:
            print(f"  - {t.name}")
        if len(tools) > 8:
            print(f"  ... (+{len(tools) - 8} more)")
    finally:
        await toolset.close()


if __name__ == "__main__":
    asyncio.run(main())
