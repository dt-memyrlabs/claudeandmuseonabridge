#!/usr/bin/env python3
"""claude_mcp_client.py — minimal client for claude_mcp_server.py.

Usage:
  CLAUDE_MCP_URL=http://<pc-tailscale-ip>:8765 \
  CLAUDE_MCP_KEY_FILE=~/.config/claude-bridge/mcp_client_key \
  python3 claude_mcp_client.py ask [--chat ID] [--tools A,B] "prompt"

Prints one JSON line: the server's result plus client-side elapsed.
"""
import json
import os
import sys
import time
import urllib.request

URL = os.environ.get("CLAUDE_MCP_URL", "").rstrip("/")
KEY_FILE = os.environ.get(
    "CLAUDE_MCP_KEY_FILE",
    os.path.expanduser("~/.config/claude-bridge/mcp_client_key"))


def rpc(method: str, params: dict | None = None, timeout: int = 700):
    with open(KEY_FILE, encoding="utf-8") as f:
        key = f.read().strip()
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(
        URL + "/mcp", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "Authorization": f"Bearer {key}"})
    # Route through the runtime's tailnet proxy (the egress proxy on
    # its default port cannot route to tailnet addresses; the same
    # proxy on port 3130 can — the trick the windows-mcp client uses).
    base = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    proxy = base.rsplit(":", 1)[0] + ":3130" if base else None
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def main() -> None:
    if not URL:
        print(json.dumps({"ok": False, "error": "CLAUDE_MCP_URL not set"}))
        return
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ask"
    if cmd == "tools":
        print(json.dumps(rpc("tools/list")))
        return
    args = sys.argv[2:]
    chat_id = None
    tools = None
    fresh = False
    prompt_parts = []
    i = 0
    while i < len(args):
        if args[i] == "--chat" and i + 1 < len(args):
            chat_id = args[i + 1]; i += 2
        elif args[i] == "--tools" and i + 1 < len(args):
            tools = [t.strip() for t in args[i + 1].split(",") if t.strip()]
            i += 2
        elif args[i] == "--fresh":
            fresh = True; i += 1
        else:
            prompt_parts.append(args[i]); i += 1
    prompt = " ".join(prompt_parts) or sys.stdin.read()
    rpc("initialize", {"protocolVersion": "2025-03-26",
                       "capabilities": {},
                       "clientInfo": {"name": "claude-mcp-client",
                                      "version": "0.1.0"}}, timeout=30)
    t0 = time.time()
    resp = rpc("tools/call", {"name": "claude_ask",
                              "arguments": {"prompt": prompt,
                                            "chat_id": chat_id,
                                            "allowed_tools": tools,
                                            "fresh": fresh}})
    elapsed = round(time.time() - t0, 1)
    try:
        inner = json.loads(resp["result"]["content"][0]["text"])
    except Exception:
        inner = {"ok": False, "error": "bad envelope", "raw": str(resp)[:400]}
    inner["client_elapsed_s"] = elapsed
    print(json.dumps(inner))


if __name__ == "__main__":
    main()
