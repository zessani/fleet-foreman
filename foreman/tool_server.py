"""A minimal read-only MCP server over stdio (newline-delimited JSON-RPC 2.0).

Started by the Claude Code CLI as: python tool_server.py <snapshot.json> <calls.jsonl>
Every tool answers with frozen data from the snapshot. Each tools/call is logged
to calls.jsonl so the client can report which tools the model really called.
Nothing but protocol messages may be written to stdout.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def input_schema(tool: dict) -> dict:
    props: dict[str, Any] = {}
    if tool.get("select_by"):
        props[tool["select_by"]] = {"type": "string", "description": "optional: pick one entry"}
    return {"type": "object", "properties": props}


def answer_for(tool: dict, args: dict) -> Any:
    data, key = tool.get("data"), tool.get("select_by")
    if key and isinstance(data, dict):
        picked = args.get(key)
        if isinstance(picked, str) and picked in data:
            return data[picked]
    return data


class Server:
    def __init__(self, tools: list[dict], calls_path: str) -> None:
        self.tools = {t["name"]: t for t in tools}
        self.calls_path = calls_path

    def log_call(self, name: str, args: dict) -> None:
        with open(self.calls_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"name": name, "args": args}) + "\n")

    def call_tool(self, params: dict) -> dict:
        name, args = params.get("name"), params.get("arguments") or {}
        self.log_call(str(name), args)
        tool = self.tools.get(name)
        if tool is None:
            return {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}
        text = json.dumps(answer_for(tool, args))
        return {"content": [{"type": "text", "text": text}], "isError": False}

    def handle(self, msg: dict) -> dict | None:
        """Return the reply for a request, or None for a notification."""
        if "id" not in msg:
            return None
        method, params = msg.get("method"), msg.get("params") or {}
        result: dict | None
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", "2024-11-05"),
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "foreman", "version": "1"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{"name": t["name"], "description": t.get("description", ""),
                                 "inputSchema": input_schema(t)} for t in self.tools.values()]}
        elif method == "tools/call":
            result = self.call_tool(params)
        else:
            return {"jsonrpc": "2.0", "id": msg["id"],
                    "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": msg["id"], "result": result}


def main(argv: list[str]) -> None:
    with open(argv[1], encoding="utf-8") as f:
        tools = json.load(f)["tools"]
    server = Server(tools, argv[2])
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = server.handle(json.loads(line))
        except Exception as exc:  # keep serving; one bad message must not kill the server
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(exc)[:200]}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main(sys.argv)
