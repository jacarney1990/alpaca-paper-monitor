"""AI Studio API. Isolated from the existing paper-trading dashboard."""
import hmac
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from engine import PROVIDERS, ROLES, run_task, status as provider_status
from finance_bridge import BridgeUnavailable, latest_workflows

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50000
DB = Path(os.getenv("STUDIO_DB_PATH", "studio.sqlite3"))
_LOCK = threading.Lock()

def connection():
    db = sqlite3.connect(str(DB), timeout=20)
    db.row_factory = sqlite3.Row
    return db

def initialize():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS jobs "
                   "(id TEXT PRIMARY KEY, created TEXT NOT NULL, topic TEXT NOT NULL, "
                   "workflow TEXT NOT NULL, mode TEXT NOT NULL, role TEXT NOT NULL, "
                   "providers TEXT NOT NULL, status TEXT NOT NULL, output TEXT)")
initialize()

# Deployment diagnostics show only which credential NAMES are configured.
# Never print secret values, user prompts, or provider response bodies.
_present_names = sorted(name for name, info in provider_status().items() if info["configured"])
app.logger.warning(
    "STUDIO_PROVIDER_KEY_PRESENCE providers=%s; api_usage_enabled=%s; api_calls_performed=false",
    ",".join(_present_names) or "none",
    os.getenv("STUDIO_ALLOW_API_USAGE", "false").lower() == "true",
)

def authorized():
    expected = os.getenv("STUDIO_ACCESS_TOKEN", "")
    supplied = request.headers.get("X-Studio-Token", "")
    return bool(expected) and hmac.compare_digest(expected, supplied)

def secured():
    if not authorized():
        return jsonify({"error": "Unauthorized: enter the private Studio access token."}), 401
    return None

@app.after_request
def secure_headers(response):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
    return response

@app.get("/")
def home():
    return render_template("index.html")

@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "multi-ai-intelligence-studio",
                    "trade_execution": "DISABLED",
                    "api_usage_enabled": os.getenv("STUDIO_ALLOW_API_USAGE", "false").lower() == "true"})

@app.get("/api/providers")
def providers():
    reject = secured()
    if reject is not None:
        return reject
    return jsonify({"providers": provider_status(),
                    "api_usage_enabled": os.getenv("STUDIO_ALLOW_API_USAGE", "false").lower() == "true"})

@app.get("/api/finance/status")
def finance_status():
    reject = secured()
    if reject is not None:
        return reject
    try:
        return jsonify(latest_workflows())
    except BridgeUnavailable as exc:
        return jsonify({"error": str(exc)}), 503

@app.get("/api/jobs")
def jobs():
    reject = secured()
    if reject is not None:
        return reject
    with connection() as db:
        records = db.execute(
            "SELECT id, created, topic, workflow, mode, role, providers, status "
            "FROM jobs ORDER BY created DESC LIMIT 30").fetchall()
    return jsonify([dict(row) for row in records])

@app.get("/api/jobs/<job_id>")
def job(job_id):
    reject = secured()
    if reject is not None:
        return reject
    with connection() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return jsonify({"error": "Not found"}), 404
    result = dict(row)
    result["providers"] = json.loads(result["providers"])
    result["output"] = json.loads(result["output"]) if result["output"] else None
    return jsonify(result)

def worker(job_id, topic, mode, role, selected, workflow):
    try:
        result = run_task(topic, mode, role, selected, workflow)
        outcome = "complete" if result["completed"] else "failed"
    except Exception as exc:
        # Do not return/log provider body, keys or user task content in diagnostics.
        result = {"error": "Unexpected worker failure", "type": type(exc).__name__}
        outcome = "failed"
    with _LOCK, connection() as db:
        db.execute("UPDATE jobs SET status=?, output=? WHERE id=?",
                   (outcome, json.dumps(result), job_id))

@app.post("/api/jobs")
def create_job():
    reject = secured()
    if reject is not None:
        return reject
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected JSON"}), 400
    topic = data.get("topic", "")
    mode = data.get("mode", "team")
    role = data.get("role", "Researcher")
    selected = data.get("providers", [])
    workflow = data.get("workflow", "research")
    if not isinstance(topic, str) or not 3 <= len(topic.strip()) <= 5000:
        return jsonify({"error": "Task must contain 3–5000 characters"}), 400
    if mode not in ("independent", "team") or role not in ROLES or workflow not in (
            "investment", "research", "project"):
        return jsonify({"error": "Invalid workflow, mode or role"}), 400
    if (not isinstance(selected, list) or not selected or len(selected) > 4 or
            any(not isinstance(p, str) or p not in PROVIDERS for p in selected) or
            len(set(selected)) != len(selected)):
        return jsonify({"error": "Select one to four distinct AI providers"}), 400
    current = provider_status()
    if any(not current[p]["configured"] for p in selected):
        return jsonify({"error": "Selected provider is not configured on the server."}), 422
    if os.getenv("STUDIO_ALLOW_API_USAGE", "false").lower() != "true":
        return jsonify({"error": "AI calls disabled until explicitly authorized in Render. "
                                  "No provider requests have been sent."}), 403
    with _LOCK, connection() as db:
        count = db.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]
        if count >= 2:
            return jsonify({"error": "Two missions are already running."}), 429
        job_id = str(uuid.uuid4())
        db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",
                   (job_id, datetime.now(timezone.utc).isoformat(), topic.strip(), workflow,
                    mode, role, json.dumps(selected), "running", None))
    threading.Thread(target=worker,
                     args=(job_id, topic.strip(), mode, role, selected, workflow),
                     daemon=True).start()
    return jsonify({"id": job_id, "status": "running"}), 202

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
