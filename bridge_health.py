#!/usr/bin/env python3
"""Bridge health probe: one short chat run through ask_claude.py.

Prints exactly one line:
  OK
  FAIL: <reason>
Exit code 0 on OK, 1 on FAIL. No chat id is passed, so the probe
starts a fresh Claude session and stores nothing.
"""
import json
import subprocess
import sys

import os

HERE = os.path.dirname(os.path.abspath(__file__))
ASK = os.path.join(HERE, "ask_claude.py")

def main() -> int:
    try:
        r = subprocess.run(
            [sys.executable, ASK],
            input="Reply with exactly: HEALTH-OK",
            capture_output=True, text=True, timeout=600)
        data = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:
        print(f"FAIL: probe itself errored: {type(e).__name__}: {e}")
        return 1
    if data.get("ok") and "HEALTH-OK" in (data.get("result") or ""):
        try:
            import time
            stamp = os.path.join(HERE, "bridge_last_ok.txt")
            with open(stamp, "w", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%dT%H:%M:%S%z") + "\n")
        except Exception:
            pass
        print("OK")
        return 0
    print(f"FAIL: {data.get('error') or 'unexpected reply: ' + str(data.get('result'))[:120]}")
    return 1

if __name__ == "__main__":
    sys.exit(main())
