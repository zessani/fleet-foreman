import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from foreman import llm
from foreman.ai_base import DATA_READ, TOOLS_CALLED, Tool
from foreman.llm import ClaudeCodeClient, FakeModel, get_client, load_env_file

SERVER = Path(llm.__file__).with_name("tool_server.py")
SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}}


# ---- load_env_file -------------------------------------------------------

def test_load_env_file_sets_only_unset_and_returns_names(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text('# comment\n\nexport A_ONE="secret-one"\nA_TWO=\'two\'\nA_THREE=three\nA_SET=new\n')
    for n in ("A_ONE", "A_TWO", "A_THREE"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("A_SET", "old")
    names = load_env_file(f)
    assert names == ["A_ONE", "A_TWO", "A_THREE"]
    assert os.environ["A_ONE"] == "secret-one"
    assert os.environ["A_TWO"] == "two"
    assert os.environ["A_SET"] == "old"
    for n in names:
        monkeypatch.delenv(n, raising=False)


def test_load_env_file_never_returns_values(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("A_SECRET=hunter2hunter2\n")
    monkeypatch.delenv("A_SECRET", raising=False)
    names = load_env_file(f)
    assert "hunter2hunter2" not in repr(names)
    monkeypatch.delenv("A_SECRET", raising=False)


def test_load_env_file_missing(tmp_path):
    assert load_env_file(tmp_path / "nope") == []


# ---- get_client ----------------------------------------------------------

def test_get_client_off_without_token(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("FOREMAN_AI", raising=False)
    assert get_client() is None


def test_get_client_off_when_disabled(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
    monkeypatch.setenv("FOREMAN_AI", "0")
    assert get_client() is None


def test_get_client_on(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
    monkeypatch.delenv("FOREMAN_AI", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda _: "/bin/claude")
    assert isinstance(get_client(), ClaudeCodeClient)


# ---- FakeModel -----------------------------------------------------------

def test_fake_model_answers_and_records():
    m = FakeModel([{"a": 1}, None, RuntimeError("boom")])
    tools = [Tool("t1", "d", {}), Tool("t2", "d", {})]
    r1 = m.ask("sys", "p", tools, SCHEMA)
    assert r1.ok and r1.answer == {"a": 1}
    assert [t["name"] for t in r1.tools] == ["t1", "t2"]
    r2 = m.ask("sys", "p", [], SCHEMA)
    assert not r2.ok and r2.error == "no structured answer"
    r3 = m.ask("sys", "p", [], SCHEMA)
    assert not r3.ok and r3.error == "boom"
    r4 = m.ask("sys", "p", [], SCHEMA)
    assert r4.error == "no more fake answers"
    assert m.calls[0] == {"system": "sys", "prompt": "p", "tools": ["t1", "t2"], "schema": SCHEMA}
    assert len(m.calls) == 4


def test_fake_model_timeout():
    m = FakeModel([{"a": 1}], delay_s=0.5)
    r = m.ask("s", "p", [], SCHEMA, timeout_s=0.05)
    assert not r.ok and r.error.startswith("timed out")
    assert r.ms < 400


# ---- tool server ---------------------------------------------------------

def run_server(tmp_path, messages):
    snap = tmp_path / "snap.json"
    calls = tmp_path / "calls.jsonl"
    snap.write_text(json.dumps({"tools": [
        {"name": "all", "description": "everything", "data": {"a": 1}, "select_by": None},
        {"name": "pick", "description": "one", "data": {"k1": [1], "k2": [2]}, "select_by": "key"},
    ]}))
    stdin = "".join(json.dumps(m) + "\n" for m in messages)
    out = subprocess.run([sys.executable, str(SERVER), str(snap), str(calls)], input=stdin,
                         capture_output=True, text=True, timeout=20)
    replies = [json.loads(line) for line in out.stdout.splitlines()]
    logged = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
    return replies, logged


def test_tool_server_protocol(tmp_path):
    def call(i, name, args=None):
        return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                "params": {"name": name, "arguments": args or {}}}
    replies, logged = run_server(tmp_path, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        call(4, "all"),
        call(5, "pick", {"key": "k2"}),
        call(6, "pick"),
        call(7, "nope"),
        {"jsonrpc": "2.0", "id": 8, "method": "bogus"},
    ])
    by_id = {r["id"]: r for r in replies}
    assert len(replies) == 8  # the notification got no reply
    assert by_id[1]["result"]["protocolVersion"] == "2025-03-26"
    assert by_id[1]["result"]["serverInfo"]["name"] == "foreman"
    assert by_id[2]["result"] == {}
    tools = {t["name"]: t for t in by_id[3]["result"]["tools"]}
    assert tools["all"]["inputSchema"] == {"type": "object", "properties": {}}
    assert tools["pick"]["inputSchema"]["properties"]["key"]["type"] == "string"
    text = lambda i: json.loads(by_id[i]["result"]["content"][0]["text"])
    assert text(4) == {"a": 1}
    assert text(5) == [2]
    assert text(6) == {"k1": [1], "k2": [2]}
    assert by_id[7]["result"]["isError"] is True
    assert by_id[8]["error"]["code"] == -32601
    assert [c["name"] for c in logged] == ["all", "pick", "pick", "nope"]
    assert logged[1]["args"] == {"key": "k2"}


# ---- ClaudeCodeClient with a fake `claude` -------------------------------

def install_fake_claude(tmp_path, monkeypatch, body: str):
    exe = tmp_path / "claude"
    exe.write_text(f"#!{sys.executable}\nimport sys, os, json, time\n{body}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")


def test_client_good_answer(tmp_path, monkeypatch):
    install_fake_claude(tmp_path, monkeypatch,
                        'print(json.dumps({"is_error": False, "structured_output": {"x": "hi"}}))')
    r = ClaudeCodeClient(tools_mode="prompt").ask("s", "p", [Tool("t", "d", {"a": 1})], SCHEMA)
    assert r.ok and r.answer == {"x": "hi"}
    assert r.tools == [{"name": "t", "args": {}}] and r.tools_mode == DATA_READ


def test_client_server_mode_reads_calls(tmp_path, monkeypatch):
    body = (
        "a = sys.argv\n"
        "cfg = json.loads(a[a.index('--mcp-config') + 1])\n"
        "calls = cfg['mcpServers']['foreman']['args'][2]\n"
        "open(calls, 'a').write(json.dumps({'name': 't', 'args': {}}) + '\\n')\n"
        "assert 'mcp__foreman__t' in a\n"
        "print(json.dumps({'is_error': False, 'structured_output': {'x': 'ok'}}))"
    )
    install_fake_claude(tmp_path, monkeypatch, body)
    r = ClaudeCodeClient(tools_mode="server").ask("s", "p", [Tool("t", "d", {})], SCHEMA)
    assert r.ok and r.tools == [{"name": "t", "args": {}}] and r.tools_mode == TOOLS_CALLED


def test_client_is_error(tmp_path, monkeypatch):
    install_fake_claude(tmp_path, monkeypatch,
                        'print(json.dumps({"is_error": True, "result": "limit reached"}))')
    r = ClaudeCodeClient(tools_mode="prompt").ask("s", "p", [], SCHEMA)
    assert not r.ok and r.answer is None and "limit reached" in r.error


def test_client_no_structured_output(tmp_path, monkeypatch):
    install_fake_claude(tmp_path, monkeypatch, 'print(json.dumps({"is_error": False, "result": "hm"}))')
    r = ClaudeCodeClient(tools_mode="prompt").ask("s", "p", [], SCHEMA)
    assert not r.ok and r.error == "no structured answer"


def test_client_bad_json(tmp_path, monkeypatch):
    install_fake_claude(tmp_path, monkeypatch, 'print("not json")')
    r = ClaudeCodeClient(tools_mode="prompt").ask("s", "p", [], SCHEMA)
    assert not r.ok and r.error == "bad JSON from the CLI"


def test_client_nonzero_exit(tmp_path, monkeypatch):
    install_fake_claude(tmp_path, monkeypatch, "sys.exit(1)")
    r = ClaudeCodeClient(tools_mode="prompt").ask("s", "p", [], SCHEMA)
    assert not r.ok and r.error == "exit code 1"


def test_client_timeout(tmp_path, monkeypatch):
    install_fake_claude(tmp_path, monkeypatch, "time.sleep(30)")
    r = ClaudeCodeClient(tools_mode="prompt").ask("s", "p", [], SCHEMA, timeout_s=1)
    assert not r.ok and r.error == "timed out after 1 s"
    assert 900 <= r.ms < 5000


def test_client_strips_api_credentials(tmp_path, monkeypatch):
    install_fake_claude(tmp_path, monkeypatch, (
        "names = sorted(os.environ)\n"
        "print(json.dumps({'is_error': False, 'structured_output': {'names': names}}))"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "keep")
    r = ClaudeCodeClient(tools_mode="prompt").ask("s", "p", [], SCHEMA)
    names = r.answer["names"]
    assert "ANTHROPIC_API_KEY" not in names and "ANTHROPIC_AUTH_TOKEN" not in names
    assert "CLAUDE_CODE_OAUTH_TOKEN" in names


def test_client_defaults(monkeypatch):
    monkeypatch.delenv("FOREMAN_LLM_MODEL", raising=False)
    monkeypatch.delenv("FOREMAN_AI_TOOLS", raising=False)
    c = ClaudeCodeClient()
    assert c.name == "claude-haiku-5-5 via Claude Code"
    assert c.tools_mode == llm.DEFAULT_TOOLS_MODE
