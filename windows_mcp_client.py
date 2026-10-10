#!/usr/bin/env python3
"""MCP client for a windows-mcp server on the user's PC (via Tailscale).

Usage:
    client.py list-tools
    client.py call <tool-name> '{"arg": "value"}'

The auth key lives in ~/.config/windows-mcp-client/auth_key (mode 600)
and is never printed or logged. Traffic goes through the runtime Tailscale
proxy (port 3130). Target: set WINDOWS_MCP_URL to your PC's tailnet MCP endpoint.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

KEY_FILE = Path.home() / ".config" / "windows-mcp-client" / "auth_key"
SERVER_URL = os.environ.get("WINDOWS_MCP_URL", "http://<PC-TAILNET-IP>:8000/mcp/")


def tailnet_proxy() -> str:
    base = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    if not base:
        raise RuntimeError("No HTTPS_PROXY/HTTP_PROXY in environment; cannot reach tailnet")
    host = base.rsplit(":", 1)[0]
    return f"{host}:3130"


def read_key() -> str:
    key = KEY_FILE.read_text().strip()
    if not key:
        raise RuntimeError(f"Empty key file: {KEY_FILE}")
    return key


def make_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        proxy=tailnet_proxy(),
        headers={"Authorization": f"Bearer {read_key()}"},
        timeout=httpx.Timeout(30.0),
    )


async def list_tools() -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with make_http_client() as http_client:
        async with streamable_http_client(SERVER_URL, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                for t in tools.tools:
                    desc = (t.description or "").split("\n")[0][:100]
                    print(f"- {t.name}: {desc}")


async def call_tool(name: str, args_json: str) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    args = json.loads(args_json) if args_json else {}
    async with make_http_client() as http_client:
        async with streamable_http_client(SERVER_URL, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, args)
                for block in result.content:
                    if hasattr(block, "text"):
                        print(block.text)
                    else:
                        print(str(block)[:2000])


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-tools")
    call = sub.add_parser("call")
    call.add_argument("name")
    call.add_argument("args", nargs="?", default="{}")
    ns = ap.parse_args()
    try:
        if ns.cmd == "list-tools":
            asyncio.run(list_tools())
        else:
            asyncio.run(call_tool(ns.name, ns.args))
    except Exception as e:
        # Never leak the key in tracebacks; it only lives in the header.
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
