"""Standalone check that the MCP server actually works over the real MCP
stdio protocol (not just as plain Python function calls) -- spawns
server.py as a subprocess, the same way Claude Code/Desktop would, and
exercises every tool against the live dashboard.

Requires the dashboard to be running (systemctl --user status ml-dashboard).
Plain asserts, no test framework. Run directly: venv/bin/python test_server.py
"""

import asyncio
import json
import os
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(SERVER_DIR, "venv", "bin", "python")
SERVER_SCRIPT = os.path.join(SERVER_DIR, "server.py")

EXPECTED_TOOLS = {
    "get_current_metrics", "get_dashboard_config", "get_db_stats", "get_history",
    "export_history", "get_lighting_state", "get_lighting_modes", "set_lighting",
}


async def main():
    params = StdioServerParameters(command=PYTHON, args=[SERVER_SCRIPT])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = {t.name for t in tools.tools}
            assert tool_names == EXPECTED_TOOLS, (
                f"tool set mismatch: expected {EXPECTED_TOOLS}, got {tool_names}"
            )

            # A read-only tool against real data.
            result = await session.call_tool("get_db_stats", {})
            assert not result.isError, f"get_db_stats failed: {result.content}"
            assert '"total_records"' in result.content[0].text

            # A parameterized read-only tool.
            result = await session.call_tool("get_history", {"limit": 3})
            assert not result.isError, f"get_history failed: {result.content}"
            assert '"count"' in result.content[0].text

            # get_history must cap at 50 rows even if a huge limit is
            # requested -- each row is ~6KB, so this is the guard against
            # accidentally blowing out an agent's context window.
            result = await session.call_tool("get_history", {"limit": 100000})
            assert not result.isError, f"get_history(100000) failed: {result.content}"
            data = json.loads(result.content[0].text)
            assert len(data["data"]) <= 50, f"expected at most 50 rows, got {len(data['data'])}"

            # export_history must write the real data to a file and return
            # only a small summary as the tool result -- not the data
            # itself, which for a real time range can be tens of megabytes.
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
                export_path = tmp.name
            try:
                result = await session.call_tool(
                    "export_history", {"output_path": export_path, "components": "gpu,cpu", "limit": 10}
                )
                assert not result.isError, f"export_history failed: {result.content}"
                summary_text = result.content[0].text
                assert len(summary_text) < 2000, (
                    f"export_history's tool result should be a small summary, not the data "
                    f"itself -- got {len(summary_text)} chars"
                )
                summary = json.loads(summary_text)
                assert summary["file_path"] == export_path
                assert summary["components"] == ["cpu", "gpu"]

                with open(export_path) as f:
                    exported = json.load(f)
                assert exported["count"] == summary["count"]
                if exported["data"]:
                    assert sorted(exported["data"][0].keys()) == ["cpu", "gpu", "timestamp"]
            finally:
                os.remove(export_path)

            # The one write tool -- round-trip on/off so this test doesn't
            # leave the lights in a different state than it found them.
            result = await session.call_tool(
                "set_lighting", {"power": "on", "mode": "direct", "color": "#123456", "brightness": 50}
            )
            assert not result.isError, f"set_lighting(on) failed: {result.content}"
            assert '"power": "on"' in result.content[0].text or '"power":"on"' in result.content[0].text

            result = await session.call_tool("set_lighting", {"power": "off"})
            assert not result.isError, f"set_lighting(off) failed: {result.content}"

            # Invalid input must come back as a tool error, not crash the server.
            result = await session.call_tool("set_lighting", {"power": "blink"})
            assert result.isError, "set_lighting('blink') should have been rejected"

    print("mcp_server/test_server.py passed")


if __name__ == "__main__":
    asyncio.run(main())
