"""Shared fixtures for the claude-usage tests.

The script has no .py extension, so it is loaded here as the module `cu`.
Every test runs with HOME and XDG_DATA_HOME pointed at a temp dir, the
default config path moved there too, and the price fetch disabled, so no test
reads or writes your real config, warehouse or pricing file, and none goes to
the network unless it replaces the fetch itself.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "claude-usage"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_SSH_DIR = Path(__file__).resolve().parent / "bin"
PRICING_PAGE = FIXTURES / "pricing-page-2026-10-05.md"

REAL_HOME = Path(os.path.expanduser("~"))
REAL_STATE = [
    REAL_HOME / ".config" / "claude-usage" / "config.json",
    REAL_HOME / ".local" / "share" / "claude-usage" / "rows.jsonl",
    REAL_HOME / ".local" / "share" / "claude-usage" / "pricing.json",
]


def load_script():
    loader = importlib.machinery.SourceFileLoader("claude_usage", str(SCRIPT))
    spec = importlib.util.spec_from_loader("claude_usage", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cu = load_script()


def file_state(path):
    return (path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None


@pytest.fixture(scope="session", autouse=True)
def real_state_untouched():
    """Fail the run if any test changed the real config, warehouse or pricing file."""
    before = [file_state(p) for p in REAL_STATE]
    yield
    changed = [str(p) for p, b in zip(REAL_STATE, before) if file_state(p) != b]
    assert not changed, "tests modified real files: {}".format(", ".join(changed))


class NoNetwork(Exception):
    pass


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A private HOME, data dir and config path per test, and no price fetch."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setattr(cu, "CONFIG_PATH", str(home / ".config" / "claude-usage" / "config.json"))

    def no_fetch(url, timeout):
        raise NoNetwork("a test tried to fetch {}; replace cu.fetch_pricing_markdown".format(url))
    monkeypatch.setattr(cu, "fetch_pricing_markdown", no_fetch)
    return home


@pytest.fixture
def home(isolated):
    return isolated


@pytest.fixture
def config_path(home):
    return home / ".config" / "claude-usage" / "config.json"


@pytest.fixture
def warehouse_path(home):
    return home / ".local" / "share" / "claude-usage" / "rows.jsonl"


@pytest.fixture
def pricing_path(home):
    return home / ".local" / "share" / "claude-usage" / "pricing.json"


@pytest.fixture
def write_config(config_path):
    def write(doc):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(doc))
        return config_path
    return write


@pytest.fixture
def pricing_page(monkeypatch):
    """Serve the saved pricing page instead of fetching it; returns the list of fetched URLs."""
    fetched = []

    def fetch(url, timeout):
        fetched.append(url)
        return PRICING_PAGE.read_text(encoding="utf-8")
    monkeypatch.setattr(cu, "fetch_pricing_markdown", fetch)
    return fetched


# --------------------------------------------------------------------------
# Synthetic transcripts. Never commit real ones: they hold prompts and code.
# --------------------------------------------------------------------------

def record(model="claude-opus-5", msg_id="msg_1", request_id="req_1", ts="2026-09-15T10:00:00.000Z",
           input=0, output=100, write_5m=0, write_1h=0, read=0, speed=None,
           entrypoint="cli", session="session-1", cwd="/work/project", uuid=None, breakdown=True):
    """One assistant transcript record, shaped like the ones Claude Code writes."""
    usage = {
        "input_tokens": input,
        "output_tokens": output,
        "cache_creation_input_tokens": write_5m + write_1h,
        "cache_read_input_tokens": read,
    }
    if breakdown:
        usage["cache_creation"] = {"ephemeral_5m_input_tokens": write_5m, "ephemeral_1h_input_tokens": write_1h}
    if speed:
        usage["speed"] = speed
    message = {"model": model, "usage": usage}
    if msg_id is not None:
        message["id"] = msg_id
    return {
        "type": "assistant",
        "uuid": uuid or "uuid-{}-{}".format(msg_id, output),
        "requestId": request_id,
        "timestamp": ts,
        "sessionId": session,
        "cwd": cwd,
        "entrypoint": entrypoint,
        "message": message,
    }


@pytest.fixture
def projects_dir(home):
    path = home / ".claude" / "projects"
    path.mkdir(parents=True)
    return path


def write_transcript(projects_dir, name, records, extra_lines=()):
    """Write records as one JSONL transcript under projects_dir; returns its path."""
    path = Path(projects_dir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r) for r in records] + list(extra_lines)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Running the CLI in-process
# --------------------------------------------------------------------------

@pytest.fixture
def run(capsys):
    """Run cu.main(argv); returns (exit code, stdout, stderr)."""
    def call(*argv):
        code = cu.main(list(argv))
        out, err = capsys.readouterr()
        return code, out, err
    return call


@pytest.fixture
def report(run, config_path, projects_dir):
    """Run a --json report against the test's projects dir and config; returns (exit code, doc, stderr)."""
    def call(*argv):
        code, out, err = run("--json", "--config", str(config_path), "--projects-dir", str(projects_dir), *argv)
        return code, json.loads(out), err
    return call


# --------------------------------------------------------------------------
# Remote machines through the fake ssh
# --------------------------------------------------------------------------

@pytest.fixture
def fake_ssh(monkeypatch, tmp_path):
    """Put tests/bin/ssh first on PATH; returns a function reading the logged ssh calls."""
    monkeypatch.setenv("PATH", "{}{}{}".format(FAKE_SSH_DIR, os.pathsep, os.environ["PATH"]))
    log = tmp_path / "ssh.log"
    monkeypatch.setenv("FAKE_SSH_LOG", str(log))

    def calls():
        if not log.exists():
            return []
        text = log.read_text()
        return [c.strip("\n").split("\n") for c in text.split("\n--\n") if c.strip("\n")]
    return calls


def find_python38():
    """A Python 3.8 interpreter, or None: uv's managed one first, then python3.8 on PATH."""
    if shutil.which("uv"):
        p = subprocess.run(["uv", "python", "find", "3.8"], capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    return shutil.which("python3.8")


@pytest.fixture
def local_tz(monkeypatch):
    """Switch this process's local timezone; restored after the test."""
    def switch(name):
        monkeypatch.setenv("TZ", name)
        time.tzset()
    yield switch
    monkeypatch.undo()
    time.tzset()
