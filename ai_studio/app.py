"""AI Studio API. Isolated from the existing paper-trading dashboard."""
import hmac
import json
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile
import os
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file, send_from_directory
from engine import PROVIDERS, ROLES, run_task, run_demo_task, check_provider_key, status as provider_status
from finance_bridge import BridgeUnavailable, latest_workflows
from public_sources import SourceError, lookup as lookup_public_source, source_status

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
        db.execute("CREATE TABLE IF NOT EXISTS call_reservations "
                   "(day TEXT PRIMARY KEY, reserved INTEGER NOT NULL)")
        # Any job still running at service startup cannot be resumed by the in-process worker.
        db.execute("UPDATE jobs SET status='interrupted', output=? WHERE status='running'",
                   (json.dumps({"error": "Service restarted before completion. Re-run this mission."}),))
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


# PUBLIC preview and source distribution. Neither route reads environment
# secrets, session tokens, provider credentials, SQLite, or mission history.
@app.get("/demo")
def public_demo():
    return render_template("demo.html")


@app.get("/service-worker.js")
def service_worker():
    response = send_from_directory(app.static_folder, "service-worker.js",
                                   mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    return response


# Explicit allowlist: never zip environment variables, .env, SQLite databases,
# account records, Git metadata, logs, or the original brokerage-monitor app.
STUDIO_SHARE_FILES = (
    "app.py", "engine.py", "finance_bridge.py", "public_sources.py",
    "README.md", "templates/index.html", "templates/demo.html",
    "static/app.js", "static/demo.js", "static/install.js",
    "static/style.css", "static/offline.html", "static/nexus-icon.svg",
    "static/nexus-icon-192.png", "static/nexus-icon-512.png",
    "static/manifest.webmanifest", "static/demo-manifest.webmanifest",
    "static/service-worker.js",
    "tools/build_icons.py", "tests/test_studio.py", "tests/test_public_sources.py",
)
STUDIO_DOWNLOAD_README = """# Nexus AI Studio: standalone source package

This ZIP contains the isolated Nexus Studio web application, safe demo,
official SEC/FDA/FRED research connectors, and offline tests.
It contains NO API keys, saved research history, access codes, credentials,
brokerage orders, or copies of the existing Alpaca investment monitor.

## Run locally or on your own Render service

1. Install Python 3.12+ and unzip this package.
2. In the extracted Nexus-AI-Studio folder run:
       python -m pip install -r requirements.txt
3. Configure a strong STUDIO_ACCESS_TOKEN as an ENVIRONMENT VARIABLE
   in your own host. Do NOT commit it to Git.
4. Start the app from the package root:
       gunicorn --chdir ai_studio --workers 1 --threads 4 --timeout 120 app:app
   For local Windows use: cd ai_studio && python app.py
5. Visit http://localhost:5000 if using python app.py (or your host URL).
   Public preview: /demo
   Private workspace: /
   Shareable ZIP: /download

API model keys are optional, and all private credentials must be configured
separately on your own server. NEVER copy another user's private keys.
STUDIO_ALLOW_API_USAGE=false and STUDIO_DAILY_CALL_CAP=0 are the safe defaults.
The public demonstration uses prewritten sample text and makes no AI calls.

The SEC/FRED GitHub snapshot connector in this source package points to the
original public repository's official-data snapshot. If you need your own
refreshes, set up an equivalent government-source workflow and adjust the
fixed snapshot URL in ai_studio/public_sources.py. FDA public research can
be accessed with no key (usage limits apply).

NOTICE: This is the source ZIP, not a signed Android APK or an app-store app.
For Android installation, open the HTTPS-hosted Nexus site in Chrome and
choose Install app or Add to Home screen.
The Render free plan may use ephemeral SQLite storage, so do not rely on it
to preserve long-running jobs or mission history across restarts.
"""


@app.get("/download")
def download_studio_source():
    """Build an independent, credential-free ZIP from known tracked files."""
    root = Path(__file__).resolve().parent
    archive_bytes = BytesIO()
    with ZipFile(archive_bytes, "w", compression=ZIP_DEFLATED,
                 compresslevel=7) as archive:
        for relative in STUDIO_SHARE_FILES:
            # Only the static, human-reviewed allowlist may enter the ZIP.
            file_path = root / relative
            archive.write(file_path, "Nexus-AI-Studio/ai_studio/" + relative)
        archive.write(root.parent / "requirements.txt",
                      "Nexus-AI-Studio/requirements.txt")
        archive.writestr("Nexus-AI-Studio/README-START-HERE.md",
                         STUDIO_DOWNLOAD_README)
        archive.writestr("Nexus-AI-Studio/.env.example",
                         "STUDIO_ACCESS_TOKEN=CHOOSE_A_PRIVATE_RANDOM_PASSWORD\n"
                         "STUDIO_ALLOW_API_USAGE=false\n"
                         "STUDIO_DAILY_CALL_CAP=0\n"
                         "OPENAI_API_KEY=\nGEMINI_API_KEY=\nGROQ_API_KEY=\n"
                         "ANTHROPIC_API_KEY=\nOPENROUTER_API_KEY=\n"
                         "FRED_API_KEY=\nOPENFDA_API_KEY=\n"
                         "SEC_USER_AGENT=\n")
    archive_bytes.seek(0)
    result = send_file(archive_bytes, as_attachment=True,
                       download_name="Nexus-AI-Studio-Source.zip",
                       mimetype="application/zip")
    result.headers["X-Nexus-Package"] = "Public-source-only; excludes secrets and data"
    return result


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "multi-ai-intelligence-studio",
                    "trade_execution": "DISABLED",
                    "api_usage_enabled": os.getenv("STUDIO_ALLOW_API_USAGE", "false").lower() == "true"})

def limits():
    raw = os.getenv("STUDIO_DAILY_CALL_CAP", "0")
    try:
        cap = int(raw)
    except ValueError:
        cap = 0
    cap = max(0, min(cap, 100))
    today = datetime.now(timezone.utc).date().isoformat()
    with connection() as db:
        row = db.execute("SELECT reserved FROM call_reservations WHERE day=?", (today,)).fetchone()
    used = row["reserved"] if row else 0
    return {"daily_limit": cap, "reserved_today": used, "remaining": max(0, cap-used),
            "api_usage_enabled": os.getenv("STUDIO_ALLOW_API_USAGE", "false").lower() == "true"}


@app.get("/api/self-test")
def self_test():
    reject = secured()
    if reject is not None:
        return reject
    try:
        with connection() as db:
            db.execute("SELECT 1").fetchone()
        storage = "available"
    except sqlite3.Error:
        storage = "unavailable"
    configured = sorted(name for name, value in provider_status().items() if value["configured"])
    return jsonify({
        "service": "online", "storage": storage,
        "configured_providers": configured,
        "provider_authentication": "not tested",
        "live_model_calls_tested": False,
        "practice_mode": "available",
        "finance_bridge": "read-only", "official_sources": list(source_status()),
        "trade_execution": "DISABLED",
        "usage": limits(),
        "persistence": "ephemeral on free Render hosting; jobs may not survive restarts",
    })


@app.get("/api/providers")
def providers():
    reject = secured()
    if reject is not None:
        return reject
    return jsonify({"providers": provider_status(),
                    "api_usage_enabled": limits()["api_usage_enabled"], "usage": limits()})

@app.post("/api/providers/check")
def check_providers():
    """Owner-authenticated read-only provider metadata checks. No model tokens."""
    reject = secured()
    if reject is not None:
        return reject
    # Bound to four fixed provider hosts; no user-provided URL is accepted.
    selected = list(PROVIDERS)
    with ThreadPoolExecutor(max_workers=min(5, len(selected))) as pool:
        results = dict(zip(selected, pool.map(check_provider_key, selected)))
    return jsonify({"providers": results, "check_type": "credential_metadata_only",
                    "model_inference_performed": False, "brokerage_orders": 0})


@app.get("/api/sources/status")
def source_connections():
    reject = secured()
    if reject is not None:
        return reject
    return jsonify({"sources": source_status(), "trading": "DISABLED",
                    "ai_generation_enabled": False,
                    "note": "Official public datasets; separate from AI model usage."})


@app.post("/api/sources/query")
def query_public_source():
    reject = secured()
    if reject is not None:
        return reject
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a public data query."}), 400
    source = data.get("source")
    if source not in ("SEC", "FDA", "FRED"):
        return jsonify({"error": "Choose SEC, FDA or FRED."}), 400
    if not isinstance(data.get("ticker", ""), str) or not isinstance(
            data.get("query", ""), str) or not isinstance(data.get("series", ""), str):
        return jsonify({"error": "Search must be text."}), 400
    if any(len(str(data.get(k) or "")) > 80 for k in ("ticker", "query", "series")):
        return jsonify({"error": "Search is too long."}), 400
    try:
        result = lookup_public_source(source, data)
        return jsonify(result)
    except SourceError as exc:
        if exc.code in ("invalid_ticker", "invalid_search", "invalid_fda_dataset",
                        "unsupported_fred_series", "unknown_source"):
            return jsonify({"error": "Invalid input: " + exc.code}), 400
        if exc.code == "rate_limited":
            return jsonify({"error": "Official source rate limit reached. Please try later.",
                            "status": exc.code}), 429
        return jsonify({"error": "Official source unavailable: " + exc.code,
                        "status": exc.code}), 503


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

def worker(job_id, topic, mode, role, selected, workflow, demo=False):
    try:
        result = (run_demo_task(topic, mode, role, selected, workflow) if demo
                  else run_task(topic, mode, role, selected, workflow))
        label = result.get("team_status")
        outcome = ("complete" if label == "COMPLETE"
                   else "partial" if label == "PARTIAL"
                   else "failed" if label == "FAILED"
                   else "complete" if result["completed"] else "failed")
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
    demo = data.get("demo", False)
    if not isinstance(demo, bool):
        return jsonify({"error": "Invalid practice mode"}), 400
    if not isinstance(topic, str) or not 3 <= len(topic.strip()) <= 5000:
        return jsonify({"error": "Task must contain 3–5000 characters"}), 400
    if mode not in ("independent", "team") or role not in ROLES or workflow not in (
            "investment", "research", "project"):
        return jsonify({"error": "Invalid workflow, mode or role"}), 400
    if (not isinstance(selected, list) or not selected or len(selected) > 5 or
            any(not isinstance(p, str) or p not in PROVIDERS for p in selected) or
            len(set(selected)) != len(selected)):
        return jsonify({"error": "Select one to five distinct AI providers"}), 400
    if not demo:
        current = provider_status()
        if any(not current[p]["configured"] for p in selected):
            return jsonify({"error": "Selected provider key is not configured on the server."}), 422
        if os.getenv("STUDIO_ALLOW_API_USAGE", "false").lower() != "true":
            return jsonify({"error": "Live AI calls are off. Try a FREE practice mission; no provider requests were sent."}), 403
    # Reserve the full worst-case number of provider requests before launch.
    # Reserve even failed calls; never retry a run automatically.
    required = 0 if demo else ((2 * len(selected) + 1 if len(selected) > 1 else 2)
                               if mode == "team" else len(selected))
    day = datetime.now(timezone.utc).date().isoformat()
    with _LOCK, connection() as db:
        count = db.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]
        if count >= 2:
            return jsonify({"error": "Two missions are already running. Try again when one finishes."}), 429
        if not demo:
            budget = limits()
            if budget["daily_limit"] == 0 or budget["remaining"] < required:
                return jsonify({"error": "Live mission blocked by the daily AI-call safety limit. "
                                          "Use FREE Practice, or explicitly configure and approve a budget."}), 403
            db.execute("INSERT INTO call_reservations(day,reserved) VALUES(?,?) "
                       "ON CONFLICT(day) DO UPDATE SET reserved=reserved+excluded.reserved",
                       (day, required))
        job_id = str(uuid.uuid4())
        db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",
                   (job_id, datetime.now(timezone.utc).isoformat(), topic.strip(), workflow,
                    mode, role, json.dumps(selected), "running", None))
    threading.Thread(target=worker,
                     args=(job_id, topic.strip(), mode, role, selected, workflow, demo),
                     daemon=True).start()
    return jsonify({"id": job_id, "status": "running", "practice": demo, "reserved_calls": required}), 202


def run_one_time_safe_test():
    """Opt-in startup test: real HTTP/SQLite practice path plus metadata-only key checks.

    It never submits requests for AI-generated text or brokerage orders.
    Keep disabled except when explicitly checking the deployed service.
    """
    if os.getenv("STUDIO_RUN_SAFE_TEST", "false").lower() != "true":
        return
    import time
    practice = "error"
    results = {}
    model_listed = {}
    token = os.getenv("STUDIO_ACCESS_TOKEN", "")
    try:
        if not token:
            practice = "missing_access_token"
        else:
            client = app.test_client()
            response = client.post("/api/jobs", headers={"X-Studio-Token": token},
                json={"topic": "Nexus system test: simulated collaborative research",
                      "mode": "team", "workflow": "research", "role": "Researcher",
                      "providers": ["OpenAI", "Gemini", "Groq"], "demo": True})
            if response.status_code != 202:
                practice = "request_rejected_" + str(response.status_code)
            else:
                mission_id = response.get_json()["id"]
                for _ in range(40):
                    mission = client.get("/api/jobs/" + mission_id,
                                         headers={"X-Studio-Token": token})
                    if mission.status_code != 200:
                        break
                    record = mission.get_json()
                    if record["status"] != "running":
                        result = record.get("output") or {}
                        valid = (record["status"] == "complete" and
                                 result.get("simulation") is True and
                                 result.get("trade_execution") == "DISABLED" and
                                 len(result.get("initial", [])) == 3 and
                                 len(result.get("cross_review", [])) == 3 and
                                 bool(result.get("synthesis")))
                        practice = "pass" if valid else "failed"
                        break
                    time.sleep(0.1)
                else:
                    practice = "timed_out"
    except Exception as exc:
        practice = "error_" + type(exc).__name__

    # Model-list endpoints only: no generative token usage or trading operations.
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {name: executor.submit(check_provider_key, name)
                   for name in PROVIDERS}
        for name, future in futures.items():
            try:
                status = future.result(timeout=12)
                results[name] = status.get("status", "unknown")
                model_listed[name] = status.get("model_listed", "unknown")
            except Exception:
                results[name] = "check_error"
                model_listed[name] = "unknown"
    app.logger.warning(
        "STUDIO_SAFE_TEST practice=%s OpenAI=%s Gemini=%s Groq=%s Claude=%s "
        "Gemini_model_listed=%s Groq_model_listed=%s "
        "ai_inference_calls=0 brokerage_orders=0 api_usage_enabled=%s",
        practice, results.get("OpenAI"), results.get("Gemini"),
        results.get("Groq"), results.get("Claude"),
        model_listed.get("Gemini"), model_listed.get("Groq"),
        os.getenv("STUDIO_ALLOW_API_USAGE", "false").lower() == "true"
    )

run_one_time_safe_test()

LIVE_SMOKE_NAME = "nexus-first-real-ai-team"
LIVE_SMOKE_TOPIC = (
    "For a small mobile AI research dashboard, recommend three useful "
    "verification checks and one important limitation. Be concise. "
    "Separate observations from unverified assumptions."
)

def _live_smoke_worker(job_id):
    """One tiny real multi-model test; never interacts with brokerages."""
    selected_mode = os.getenv("STUDIO_RUN_LIVE_SMOKE", "false").lower()
    gemini_only = selected_mode == "gemini_only"
    duo = selected_mode == "duo"
    providers = ["Gemini"] if gemini_only else ["Gemini", "Groq"] if duo else ["OpenAI", "Gemini", "Groq"]
    diagnostic = selected_mode in ("diagnose", "gemini_only")
    mode = "independent" if diagnostic else "team"
    try:
        result = run_task(LIVE_SMOKE_TOPIC, mode, "Researcher", providers, "project")
        initial = {row["provider"]: row["status"] for row in result["initial"]}
        reviews = {row["provider"]: row["status"] for row in result["cross_review"]}
        synthesized = bool(result["synthesis"]) and not result["synthesis"].startswith(
            "Final synthesis unavailable"
        )
        complete_initial = all(initial.get(n) == "complete" for n in providers)
        complete = (complete_initial if diagnostic else
                    complete_initial and all(
                        reviews.get(n) == "complete" for n in providers
                    ) and synthesized)
        outcome = "complete" if complete else "failed"
        state = "PASS" if complete else "PARTIAL_OR_FAILED"
        # The topic is fixed and contains no user information.
        excerpt = (result["initial"][0].get("answer", "").replace("\n", " ")[:450]
                   if gemini_only and complete else
                   result["synthesis"].replace("\n", " ")[:450] if synthesized else "unavailable")
        errors = {row["provider"]: row.get("error", "") for row in result["initial"]
                  if row["status"] != "complete"}
        app.logger.warning(
            "STUDIO_LIVE_SMOKE result=%s stage=%s initial=%s errors=%s "
            "reviews=%s synthesis=%s max_requests=%d broker_orders=0 preview=%s",
            state, mode, initial, errors, reviews, synthesized,
            1 if gemini_only else 5 if duo else 3 if diagnostic else 7, excerpt
        )
    except Exception as exc:
        result = {"error": "Smoke test failed safely", "type": type(exc).__name__}
        outcome = "failed"
        app.logger.warning("STUDIO_LIVE_SMOKE result=ERROR type=%s broker_orders=0",
                           type(exc).__name__)
    with _LOCK, connection() as db:
        db.execute("UPDATE jobs SET status=?, output=? WHERE id=?",
                   (outcome, json.dumps(result), job_id))

def start_live_smoke_if_requested():
    """Run at most once per local database, only with a dedicated opt-in flag.

    Up to three independent answers, three peer reviews and one final
    synthesis. Ordinary live API calls remain disabled for users.
    """
    smoke_mode = os.getenv("STUDIO_RUN_LIVE_SMOKE", "false").lower()
    if smoke_mode not in ("true", "diagnose", "gemini_only", "duo"):
        return
    from engine import output_token_limit
    if output_token_limit() > (350 if smoke_mode == "gemini_only" else 550 if smoke_mode == "duo" else 600):
        app.logger.warning("STUDIO_LIVE_SMOKE blocked: output token cap exceeds 600")
        return
    if os.getenv("STUDIO_ALLOW_API_USAGE", "false").lower() == "true":
        app.logger.warning("STUDIO_LIVE_SMOKE blocked: ordinary API usage must remain off")
        return
    test_providers = (["Gemini"] if smoke_mode == "gemini_only"
                      else ["Gemini", "Groq"] if smoke_mode == "duo"
                      else ["OpenAI", "Gemini", "Groq"])
    for name in test_providers:
        if not provider_status()[name]["configured"]:
            app.logger.warning("STUDIO_LIVE_SMOKE blocked: missing provider %s", name)
            return
    with _LOCK, connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS one_time_smoke "
                   "(name TEXT PRIMARY KEY, job_id TEXT NOT NULL)")
        smoke_name = LIVE_SMOKE_NAME + "-" + smoke_mode
        found = db.execute("SELECT job_id FROM one_time_smoke WHERE name=?",
                           (smoke_name,)).fetchone()
        if found:
            app.logger.warning("STUDIO_LIVE_SMOKE already started; no repeat")
            return
        job_id = str(uuid.uuid4())
        db.execute("INSERT INTO one_time_smoke(name,job_id) VALUES(?,?)",
                   (smoke_name, job_id))
        db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",
                   (job_id, datetime.now(timezone.utc).isoformat(), LIVE_SMOKE_TOPIC,
                    "project", "independent" if smoke_mode != "true" else "team", "Researcher",
                    json.dumps(test_providers), "running", None))
    app.logger.warning(
        "STUDIO_LIVE_SMOKE starting once; stage=%s providers=%s "
        "max_requests=%d max_output_tokens=%d api_usage_enabled=false orders=0",
        smoke_mode, ",".join(test_providers),
        1 if smoke_mode == "gemini_only" else 5 if smoke_mode == "duo"
        else 3 if smoke_mode == "diagnose" else 7,
        output_token_limit()
    )
    threading.Thread(target=_live_smoke_worker, args=(job_id,), daemon=True).start()

start_live_smoke_if_requested()


def run_public_source_smoke_if_requested():
    """Opt-in source-only deployment check. No AI or brokerage calls."""
    if os.getenv("STUDIO_RUN_SOURCE_SMOKE", "false").lower() != "true":
        return
    inputs = {
        "SEC": {"ticker": "CRSP"},
        "FDA": {"dataset": "drug_recalls", "query": "aspirin"},
        "FRED": {"series": "UNRATE"},
    }
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {name: executor.submit(lookup_public_source, name, payload)
                   for name, payload in inputs.items()}
        results = {}
        for name, future in futures.items():
            try:
                report = future.result(timeout=22)
                results[name] = (report["status"], len(report.get("items", [])))
            except SourceError as exc:
                results[name] = (exc.code, 0)
            except Exception:
                results[name] = ("unexpected_source_error", 0)
    app.logger.warning(
        "STUDIO_PUBLIC_SOURCES SEC=%s FDA=%s FRED=%s "
        "ai_inference_calls=0 broker_orders=0",
        results["SEC"], results["FDA"], results["FRED"]
    )

run_public_source_smoke_if_requested()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
