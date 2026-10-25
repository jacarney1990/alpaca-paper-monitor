# Intelligence OS

A clean-room, modular foundation for a multi-provider AI research workspace. This application is separate from the legacy Nexus Flask app.

## Foundation
- FastAPI service with health, capabilities and research-plan endpoints.
- Explicit Collector, Analyst, Validator and Synthesizer roles.
- Provider adapter contract; no API credentials or provider calls wired yet.
- Read-only research by default. Brokerage execution is prohibited.
- No paid API calls or autonomous orders.
- Deploy independently; do not change the existing Render service until tests pass.

## Local setup
```bash
pip install -r intelligence_os/requirements.txt
uvicorn intelligence_os.main:app --host 127.0.0.1 --port 8001
pytest intelligence_os/tests
```

## Next milestones
1. PostgreSQL schema and migrations for projects, jobs, evidence, agent outputs and audit logs.
2. Authentication and per-user access controls.
3. Provider adapters, budgets and timeout handling.
4. Source connectors with provenance and deterministic verification.
5. React mobile-first frontend and worker queue.
6. CI tests, staging deployment, security review and migration.

No credentials should be committed. The service is not production-ready.
