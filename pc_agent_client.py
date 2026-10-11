#!/usr/bin/env python3
"""pc_agent_client.py — minimal client for pc_agent_server.py.

Usage:
  MEMYR_AGENT_URL=http://<pc-tailscale-ip>:8766 \
  python3 pc_agent_client.py ping
  python3 pc_agent_client.py exec "PowerShell command" [timeout_s]
  python3 pc_agent_client.py read <path>
  python3 pc_agent_client.py write <path> <content>
  python3 pc_agent_client.py tools

The key file defaults to the Claude bridge's client key
(~/.config/claude-bridge/mcp_client_key): the PC agent reads the
matching server key file, so no new secret exists.
"""
import json
import os
import sys
import urllib.request

URL = os.environ.get("MEMYR_AGENT_URL", "").rstrip("/")
KEY_FILE = os.environ.get(
    "MEMYR_AGENT_KEY_FILE",
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
    # proxy on port 3130 can).
    base = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    proxy = base.rsplit(":", 1)[0] + ":3130" if base else None
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def call(name: str, arguments: dict, timeout: int = 700) -> dict:
    r = rpc("tools/call", {"name": name, "arguments": arguments},
            timeout=timeout)
    if "error" in r:
        return {"ok": False, "error": r["error"]}
    text = r["result"]["content"][0]["text"]
    return json.loads(text)


def main() -> None:
    if not URL:
        print(json.dumps({"ok": False, "error": "MEMYR_AGENT_URL not set"}))
        return
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ping"
    if cmd == "tools":
        print(json.dumps(rpc("tools/list")))
    elif cmd == "ping":
        print(json.dumps(call("ping", {})))
    elif cmd == "exec":
        timeout = int(sys.argv[3]) if len(sys.argv) > 3 else 60
        print(json.dumps(call("exec", {"command": sys.argv[2],
                                       "timeout_s": timeout},
                              timeout=timeout + 30)))
    elif cmd == "read":
        print(json.dumps(call("read_file", {"path": sys.argv[2]})))
    elif cmd == "write":
        print(json.dumps(call("write_file", {"path": sys.argv[2],
                                             "content": sys.argv[3]})))
    else:
        print(json.dumps({"ok": False, "error": f"unknown command: {cmd}"}))


if __name__ == "__main__":
    main()
