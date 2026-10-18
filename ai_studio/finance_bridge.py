"""Read-only bridge to the existing finance intelligence workflows on GitHub Actions.

This module cannot trigger workflows, edit a repository, or place paper/live orders.
It reports public GitHub Actions metadata only; it does not assert market-data freshness.
"""
from __future__ import annotations
import threading
import time
from datetime import datetime, timezone
import requests

REPOSITORY = "jacarney1990/alpaca-paper-monitor"
WORKFLOWS = {
    ".github/workflows/ai-team.yml": "Independent AI Finance Team",
    ".github/workflows/investment-v35.yml": "Shared AI Intelligence",
    ".github/workflows/investment-ops.yml": "Investment Operations",
    ".github/workflows/investment-paper-bridge.yml": "Paper Readiness Bridge",
    ".github/workflows/investment-paper-pilot.yml": "V4.3 Paper Shadow Pilot",
}
_CACHE_LOCK = threading.Lock()
_CACHE = {"at": 0.0, "value": None}
_CACHE_SECONDS = 180

class BridgeUnavailable(Exception):
    """Public workflow metadata could not be retrieved."""

def latest_workflows():
    now = time.monotonic()
    with _CACHE_LOCK:
        if _CACHE["value"] is not None and now - _CACHE["at"] < _CACHE_SECONDS:
            return _CACHE["value"]
    try:
        response = requests.get(
            "https://api.github.com/repos/" + REPOSITORY + "/actions/runs",
            params={"per_page": 100},
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "multi-ai-intelligence-studio/1.0"},
            timeout=10,
        )
        if not response.ok:
            raise BridgeUnavailable("GitHub workflow metadata unavailable (HTTP %d)" % response.status_code)
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("workflow_runs"), list):
            raise BridgeUnavailable("Unexpected GitHub metadata format")
    except requests.RequestException as exc:
        raise BridgeUnavailable("GitHub workflow metadata request failed") from exc
    except ValueError as exc:
        raise BridgeUnavailable("Invalid GitHub metadata JSON") from exc
    matching = {}
    for run in body["workflow_runs"]:
        if not isinstance(run, dict) or run.get("head_branch") != "main":
            continue
        path = run.get("path", "")
        if path not in WORKFLOWS or path in matching:
            continue
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        matching[path] = {
            "name": WORKFLOWS[path],
            "status": str(run.get("status") or "unknown")[:32],
            "conclusion": str(run.get("conclusion") or "pending")[:32],
            "updated": str(run.get("updated_at") or "")[:35],
            "url": "https://github.com/" + REPOSITORY + "/actions/runs/" + str(run_id),
        }
    result = {
        "repository": REPOSITORY,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "description": "Read-only GitHub Actions activity; not proof of investment performance or readiness",
        "workflows": [
            matching.get(path, {"name": name, "status": "not in latest 100 runs",
                                "conclusion": "unverified", "updated": "", "url": ""})
            for path, name in WORKFLOWS.items()
        ],
        "brokerage_execution": "DISABLED_IN_STUDIO",
    }
    with _CACHE_LOCK:
        _CACHE.update({"at": time.monotonic(), "value": result})
    return result
