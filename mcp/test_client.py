#!/usr/bin/env python3
"""Smoke-test the zynq-agentctl MCP server: spawn it over stdio, list tools, and
call a couple against the live board. Run: .env/bin/python mcp/test_client.py"""
import asyncio, os, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(os.path.dirname(HERE), ".env", "bin", "python")


async def main():
    params = StdioServerParameters(command=PY, args=[os.path.join(HERE, "server.py")])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as sess:
            await sess.initialize()
            tools = await sess.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])
            for name, args in [("board_status", {}), ("measure_freq", {}),
                               ("adapt_freq", {"target": 95000, "tol": 1500})]:
                res = await sess.call_tool(name, args)
                print(f"\n== {name}({args}) ==")
                for c in res.content:
                    print(getattr(c, "text", c))


if __name__ == "__main__":
    asyncio.run(main())
