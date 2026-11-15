"""Model clients: the Claude Code CLI (subscription token) and a fake for tests."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from foreman.ai_base import AI_TIMEOUT_S, DATA_READ, TOOLS_CALLED, ModelReply, Tool

DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_TOOLS_MODE = "server"
SERVER_PATH = Path(__file__).with_name("tool_server.py")
SERVER_HINT = ("Call the tools you need, all in one step where possible, "
               "before you give your answer.")


def load_env_file(path: Path) -> list[str]:
    """Load NAME=value lines into os.environ (unset names only). Returns names, never values."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    loaded: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded


def _cli_env() -> dict[str, str]:
    """Drop API credentials so the subscription token is the one used."""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    return env


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def _read_calls(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    calls = []
    for line in lines:
        try:
            calls.append(json.loads(line))
        except ValueError:
            continue
    return calls


def _prompt_with_data(prompt: str, tools: list[Tool]) -> str:
    parts = [prompt]
    for t in tools:
        parts.append(f"## {t.name}\n{t.description}\n{json.dumps(t.data)}")
    return "\n\n".join(parts)


class ClaudeCodeClient:
    def __init__(self, model: str | None = None, tools_mode: str | None = None) -> None:
        self.model = model or os.environ.get("FOREMAN_LLM_MODEL") or DEFAULT_MODEL
        self.name = f"{self.model} via Claude Code"
        self.tools_mode = tools_mode or os.environ.get("FOREMAN_AI_TOOLS") or DEFAULT_TOOLS_MODE

    def ask(self, system: str, prompt: str, tools: list[Tool], schema: dict,
            timeout_s: float = AI_TIMEOUT_S) -> ModelReply:
        start = time.monotonic()
        workdir = Path(tempfile.mkdtemp(prefix="foreman-ai-"))
        try:
            return self._run(system, prompt, tools, schema, timeout_s, workdir, start)
        except Exception as exc:  # never raise out of ask()
            return ModelReply(ok=False, error=f"{type(exc).__name__}: {str(exc)[:150]}",
                              ms=(time.monotonic() - start) * 1000)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _run(self, system: str, prompt: str, tools: list[Tool], schema: dict,
             timeout_s: float, workdir: Path, start: float) -> ModelReply:
        use_server = self.tools_mode == "server" and bool(tools)
        calls_path = workdir / "calls.jsonl"
        cmd = ["claude", "-p", prompt, "--model", self.model, "--output-format", "json",
               "--json-schema", json.dumps(schema), "--tools", "",
               "--strict-mcp-config", "--no-session-persistence"]
        if use_server:
            snapshot = workdir / "snapshot.json"
            snapshot.write_text(json.dumps({"tools": [
                {"name": t.name, "description": t.description, "data": t.data,
                 "select_by": t.select_by} for t in tools]}), encoding="utf-8")
            calls_path.touch()
            config = {"mcpServers": {"foreman": {"command": sys.executable, "args": [
                str(SERVER_PATH), str(snapshot), str(calls_path)]}}}
            cmd += ["--mcp-config", json.dumps(config)]
            for t in tools:
                cmd += ["--allowedTools", f"mcp__foreman__{t.name}"]
            system = f"{system}\n\n{SERVER_HINT}"
        else:
            cmd[2] = _prompt_with_data(prompt, tools)
        cmd += ["--system-prompt", system]

        proc = subprocess.Popen(cmd, cwd=workdir, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=_cli_env(), text=True, start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            proc.communicate()
            return ModelReply(ok=False, error=f"timed out after {timeout_s:g} s",
                              ms=(time.monotonic() - start) * 1000)
        ms = (time.monotonic() - start) * 1000

        if use_server:
            used, mode = _read_calls(calls_path), TOOLS_CALLED
        else:
            used, mode = [{"name": t.name, "args": {}} for t in tools], DATA_READ
        reply = ModelReply(ok=False, tools=used, tools_mode=mode, ms=ms)
        if proc.returncode != 0:
            reply.error = f"exit code {proc.returncode}"
            return reply
        try:
            data = json.loads(out)
        except ValueError:
            reply.error = "bad JSON from the CLI"
            return reply
        if not isinstance(data, dict) or data.get("is_error"):
            detail = str(data.get("result", ""))[:200] if isinstance(data, dict) else ""
            reply.error = f"CLI reported an error: {detail}".strip()
            return reply
        answer = data.get("structured_output")
        if not isinstance(answer, dict):
            reply.error = "no structured answer"
            return reply
        reply.ok, reply.answer = True, answer
        return reply


class FakeModel:
    """A scripted model for tests. Each ask() pops the next answer."""

    name = "fake model"

    def __init__(self, answers: list[dict | Exception | None] | None = None,
                 delay_s: float = 0.0, tools_mode: str = TOOLS_CALLED) -> None:
        self.answers = list(answers or [])
        self.delay_s = delay_s
        self.tools_mode = tools_mode
        self.calls: list[dict] = []

    def ask(self, system: str, prompt: str, tools: list[Tool], schema: dict,
            timeout_s: float = AI_TIMEOUT_S) -> ModelReply:
        names = [t.name for t in tools]
        self.calls.append({"system": system, "prompt": prompt, "tools": names, "schema": schema})
        used = [{"name": n, "args": {}} for n in names]
        start = time.monotonic()
        time.sleep(min(self.delay_s, timeout_s))
        ms = (time.monotonic() - start) * 1000

        def reply(**kw) -> ModelReply:
            return ModelReply(tools=used, tools_mode=self.tools_mode, ms=ms, **kw)

        if self.delay_s > timeout_s:
            return reply(ok=False, error=f"timed out after {timeout_s:g} s")
        if not self.answers:
            return reply(ok=False, error="no more fake answers")
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            return reply(ok=False, error=str(item))
        if item is None:
            return reply(ok=False, error="no structured answer")
        return reply(ok=True, answer=item)


def get_client() -> ClaudeCodeClient | None:
    if os.environ.get("FOREMAN_AI") == "0":
        return None
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or shutil.which("claude") is None:
        return None
    return ClaudeCodeClient()
