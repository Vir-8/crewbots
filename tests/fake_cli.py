"""Stands in for `claude` or `codex`: prints a fixture's lines, then behaves as the FAKE_* variables say.

FAKE_RECORD   file to write {"argv", "stdin", "env_keys", "pid"} to
FAKE_STDERR   text to print on stderr
FAKE_SLEEP    seconds to wait after printing, before exiting
FAKE_EXIT     exit code (default 0)
"""

import json
import os
import sys
import time

fixture, *argv = sys.argv[1:]
if argv[:3] == ["mcp", "list", "--json"]:  # Codex listing its MCP servers
    if record := os.environ.get("FAKE_RECORD"):
        with open(record, "w") as f:
            json.dump({"argv": argv}, f)
    print(os.environ.get("FAKE_MCP_LIST", "[]"))
    sys.exit(0)
stdin = sys.stdin.read()
if record := os.environ.get("FAKE_RECORD"):
    with open(record, "w") as f:
        json.dump({"argv": argv, "stdin": stdin, "env_keys": sorted(os.environ), "pid": os.getpid()}, f)
if fixture != "-":
    with open(fixture) as f:
        for line in f:
            print(line, end="", flush=True)
if err := os.environ.get("FAKE_STDERR"):
    print(err, file=sys.stderr, flush=True)
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
