# Multi-AI Intelligence Studio

A separate, read-only multi-agent research application. Run it on Render using the `feature/multi-ai-studio` branch without modifying the existing paper monitor.

Build: `pip install -r requirements.txt` (uses existing Flask, requests and gunicorn).
Start: `gunicorn studio_app:app --workers 1 --threads 4 --timeout 120`

Authentication: define a strong `STUDIO_ACCESS_TOKEN` in Render, then enter it in the app. Keep all provider keys server-side and never in GitHub or the browser. Configure desired AI providers with `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`; models may be overridden by `OPENAI_MODEL`, `ANTHROPIC_MODEL`, `GEMINI_MODEL`, `GROQ_MODEL`.

`STUDIO_ALLOW_API_USAGE` defaults to `false` so no billable calls run unless explicitly enabled after checking provider quotas/costs. `STUDIO_MODE` is research only. All brokerage order submission remains disabled; this app contains no brokerage endpoints.

**MVP limitations:** background tasks run in-process and task records use ephemeral SQLite on Render free hosting. Jobs may be interrupted by restarts and history may be lost on redeploy; use a durable queue/database before production. AI answers are not automatically web-verified. Provider web accounts do not confer API permissions.

## API credentials and safe migration

The original investing research team uses GitHub Actions secrets. Those secrets cannot be read back or automatically inherited by this Render service. The original Alpaca Render service is a separate deployment and does not share its environment by default. The Render connector can set new environment variables but cannot reveal or copy the existing secret values. Never put secret values in GitHub source, task descriptions, screenshots, or chat.

To enable a direct AI provider, open [AI Studio's Render settings](https://dashboard.render.com/web/srv-dbaedtbncjis73d8hm60), choose **Environment**, and set the applicable secret variable: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, or `GROQ_API_KEY`. The Studio shows which are missing. Keys alone do not permit model invocation: `STUDIO_ALLOW_API_USAGE=false` remains the explicit cost guard, and the provider's own free/paid quotas must be checked before changing it. Existing GitHub finance workflows remain separately read-only in the Studio via /api/finance/status.
