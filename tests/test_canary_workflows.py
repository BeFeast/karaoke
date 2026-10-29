"""Canary workflows run on Forgejo and report failures there (#332).

The failure-issue step used ``actions/github-script``, which the Forgejo runner
cannot resolve, so every scheduled canary failed before reaching its check.
These tests pin the replacement (``scripts/canary-failure-issue.sh``, curl
against the Forgejo issues API) and the guard that keeps the GitHub copy from
running a second set of canaries.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
SCRIPT = ROOT / "scripts/canary-failure-issue.sh"
CANARIES = {
    "yt-dlp-canary.yml": "yt-dlp canary failure",
    "gpu-keepalive.yml": "GPU keepalive canary failure",
}


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def test_no_workflow_uses_github_script():
    for path in WORKFLOWS.glob("*.yml"):
        assert "actions/github-script" not in path.read_text(), path.name


@pytest.mark.parametrize("name", sorted(CANARIES))
def test_canary_runs_only_off_github_and_reports_through_forgejo_api(name):
    wf = _workflow(name)
    # PyYAML reads the bare `on:` key as True.
    triggers = wf[True]
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers
    (job,) = wf["jobs"].values()
    assert job["if"] == "github.server_url != 'https://github.com'"
    issue_step = next(s for s in job["steps"] if s.get("name") == "Open or update failure issue")
    assert issue_step["if"] == "steps.canary.outcome == 'failure'"
    assert "uses" not in issue_step
    assert f'scripts/canary-failure-issue.sh "{CANARIES[name]}"' in issue_step["run"]
    assert issue_step["env"]["CANARY_ISSUE_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"


def test_gpu_image_build_still_dispatchable_on_github():
    wf = _workflow("gpu-image.yml")
    assert set(wf[True]) == {"workflow_dispatch"}
    assert all("if" not in job for job in wf["jobs"].values())


def test_dependabot_is_retired():
    # Dependency bumps go through Forgejo PRs; GitHub only mirrors main.
    assert not (ROOT / ".github/dependabot.yml").exists()
    assert not (WORKFLOWS / "dependabot-auto-merge.yml").exists()


def test_failure_issue_script_is_executable():
    assert os.access(SCRIPT, os.X_OK)


class FakeForgejo:
    """Minimal Forgejo issues API: search, create, comment."""

    def __init__(self, issues: list[dict]):
        self.issues = issues
        self.requests: list[tuple[str, str, dict | None]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _reply(self, code: int, payload) -> None:
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                query = dict(urllib.parse.parse_qsl(url.query))
                fake.requests.append(("GET", url.path, query))
                if self.headers.get("Authorization") != "token t0ken":
                    return self._reply(401, {"message": "unauthorized"})
                hits = [
                    i
                    for i in fake.issues
                    if i["state"] == query.get("state")
                    and query.get("q", "").lower() in i["title"].lower()
                ]
                self._reply(200, hits)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(("POST", self.path, body))
                if self.path == "/api/v1/repos/o/r/issues":
                    self._reply(201, {"number": 99, "title": body["title"]})
                else:
                    self._reply(201, {"id": 1})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def run_script():
    if not all(shutil.which(tool) for tool in ("bash", "curl", "jq")):
        pytest.skip("needs bash, curl and jq")
    servers: list[FakeForgejo] = []

    def run(issues: list[dict], token: str = "t0ken"):
        fake = FakeForgejo(issues)
        servers.append(fake)
        env = {
            "PATH": os.environ["PATH"],
            "GITHUB_API_URL": fake.url,
            "GITHUB_REPOSITORY": "o/r",
            "CANARY_ISSUE_TOKEN": token,
        }
        proc = subprocess.run(
            [str(SCRIPT), "yt-dlp canary failure", "Run: https://forgejo/run/1\n\nline `two`"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return proc, fake

    yield run
    for fake in servers:
        fake.close()


def test_opens_issue_when_none_is_open(run_script):
    proc, fake = run_script([{"number": 3, "title": "yt-dlp canary failure", "state": "closed"}])
    assert proc.returncode == 0, proc.stderr
    assert "Opened issue #99" in proc.stdout
    method, path, query = fake.requests[0]
    assert (method, path) == ("GET", "/api/v1/repos/o/r/issues")
    assert query["type"] == "issues"
    assert query["q"] == "yt-dlp canary failure"
    assert fake.requests[1] == (
        "POST",
        "/api/v1/repos/o/r/issues",
        {"title": "yt-dlp canary failure", "body": "Run: https://forgejo/run/1\n\nline `two`"},
    )


def test_comments_on_open_issue_with_exact_title(run_script):
    proc, fake = run_script(
        [
            {"number": 5, "title": "Old yt-dlp canary failure notes", "state": "open"},
            {"number": 7, "title": "yt-dlp canary failure", "state": "open"},
        ]
    )
    assert proc.returncode == 0, proc.stderr
    assert "Commented on issue #7" in proc.stdout
    assert fake.requests[1] == (
        "POST",
        "/api/v1/repos/o/r/issues/7/comments",
        {"body": "Run: https://forgejo/run/1\n\nline `two`"},
    )


def test_keyword_match_with_other_title_opens_new_issue(run_script):
    proc, fake = run_script([{"number": 5, "title": "Old yt-dlp canary failure notes", "state": "open"}])
    assert proc.returncode == 0, proc.stderr
    assert fake.requests[1][1] == "/api/v1/repos/o/r/issues"


def test_api_error_fails_the_step(run_script):
    # A rejected token -> 401 -> curl -f exits non-zero, so the step fails loudly.
    proc, fake = run_script([], token="wrong")
    assert proc.returncode != 0
    assert [method for method, _path, _body in fake.requests] == ["GET"]
