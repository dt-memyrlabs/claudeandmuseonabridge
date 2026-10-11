#!/usr/bin/env python3
"""pc_agent_server.py — Memyr's own small agent on the user's PC.

Replaces the third-party windows-mcp server for bridge work: one file,
standard library only, four tools over JSON-RPC with bearer auth.

  ping       -> liveness + version
  exec       -> run one PowerShell command, return exit/stdout/stderr
  read_file  -> read a text file (size-capped)
  write_file -> write a text file (creates parent folders)

The bearer key is read from the same key file the Claude MCP server
uses, so no new secret is created or moved: installing this adds a
listener, not a credential. The key itself is never logged.

Config (environment):
  MEMYR_AGENT_PORT      listen port (default 8766)
  MEMYR_AGENT_KEY_FILE  bearer key file (default:
                        ~/.muse-bridge/mcp_auth_key)
"""
import hmac
import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("MEMYR_AGENT_PORT", "8766"))
KEY_FILE = os.environ.get(
    "MEMYR_AGENT_KEY_FILE",
    os.path.join(os.path.expanduser("~"), ".muse-bridge", "mcp_auth_key"))
LOG_FILE = os.path.join(HERE, "memyr_pc_agent.log")
VERSION = "0.1.0"
READ_CAP = 256_000
OUT_CAP = 8_000
ERR_CAP = 2_000


def log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def read_key() -> str:
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def tool_ping(_a: dict) -> dict:
    return {"ok": True, "server": "memyr-pc-agent", "version": VERSION,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S")}


def tool_exec(a: dict) -> dict:
    cmd = a.get("command") or ""
    if not cmd.strip():
        return {"ok": False, "error": "empty command"}
    timeout = a.get("timeout_s") or 60
    try:
        timeout = max(1, min(int(timeout), 600))
    except Exception:
        timeout = 60
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-Command", cmd],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, startupinfo=si)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": f"launch failed: {e}"}
    return {"ok": r.returncode == 0, "exit": r.returncode,
            "stdout": (r.stdout or "")[-OUT_CAP:],
            "stderr": (r.stderr or "")[-ERR_CAP:]}


def tool_read_file(a: dict) -> dict:
    path = a.get("path") or ""
    if not path:
        return {"ok": False, "error": "no path"}
    try:
        with open(path, "rb") as f:
            raw = f.read(READ_CAP + 1)
        truncated = len(raw) > READ_CAP
        return {"ok": True, "content": raw[:READ_CAP].decode(
            "utf-8", errors="replace"), "truncated": truncated}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_write_file(a: dict) -> dict:
    path = a.get("path") or ""
    content = a.get("content")
    if not path or content is None:
        return {"ok": False, "error": "path and content required"}
    try:
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return {"ok": True, "bytes": len(content.encode("utf-8"))}
    except Exception as e:
        return {"ok": False, "error": str(e)}


TOOLS = [
    {"name": "ping", "description": "Liveness check.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "exec",
     "description": "Run one PowerShell command on the PC.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "command": {"type": "string"},
                         "timeout_s": {"type": "integer"}},
                     "required": ["command"]}},
    {"name": "read_file", "description": "Read a text file on the PC.",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}},
                     "required": ["path"]}},
    {"name": "write_file", "description": "Write a text file on the PC.",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"},
                                    "content": {"type": "string"}},
                     "required": ["path", "content"]}},
]
DISPATCH = {"ping": tool_ping, "exec": tool_exec,
            "read_file": tool_read_file, "write_file": tool_write_file}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code: int, obj: dict | None) -> None:
        body = b"" if obj is None else json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "server": "memyr-pc-agent"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/mcp":
            self._send(404, {"error": "not found"})
            return
        key = read_key()
        auth = self.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if not key or not hmac.compare_digest(token, key):
            self._send(401, {"error": "unauthorized"})
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._send(400, {"error": "bad request"})
            return
        method = req.get("method", "")
        rid = req.get("id")
        if method.startswith("notifications/"):
            self._send(202, None)
            return

        def reply(result=None, error=None):
            payload = {"jsonrpc": "2.0", "id": rid}
            if error is not None:
                payload["error"] = error
            else:
                payload["result"] = result
            self._send(200, payload)

        if method == "initialize":
            reply({"protocolVersion": "2025-03-26",
                   "capabilities": {"tools": {}},
                   "serverInfo": {"name": "memyr-pc-agent",
                                  "version": VERSION}})
        elif method == "ping":
            reply({})
        elif method == "tools/list":
            reply({"tools": TOOLS})
        elif method == "tools/call":
            params = req.get("params") or {}
            name = params.get("name") or ""
            fn = DISPATCH.get(name)
            if fn is None:
                reply(error={"code": -32602,
                             "message": f"unknown tool: {name}"})
                return
            res = fn(params.get("arguments") or {})
            log(f"call tool={name} ok={res.get('ok')}")
            reply({"content": [{"type": "text",
                                 "text": json.dumps(res)}],
                   "isError": not res.get("ok")})
        else:
            reply(error={"code": -32601,
                         "message": f"method not found: {method}"})


def main() -> None:
    if not read_key():
        print(f"no bearer key at {KEY_FILE}; refusing to start",
              file=sys.stderr)
        sys.exit(1)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"listening on {PORT}")
    print(f"memyr-pc-agent listening on {PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
