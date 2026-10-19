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

## Verifying the API keys without generated tokens

The authenticated **Verify saved keys** button calls only the fixed official API provider model-metadata endpoints; it does not ask a model to generate an answer or submit trades. A successful metadata response confirms that credentials were accepted for model-list access, not that a particular model can complete a mission or that provider quotas, billing, and network latency permit inference. Live missions remain denied unless both `STUDIO_ALLOW_API_USAGE=true` and a positive `STUDIO_DAILY_CALL_CAP` are separately configured after explicit approval. Every attempted live mission reserves a worst-case number of requests up front. The daily quota is stored in ephemeral SQLite on Render's free plan and cannot guarantee a durable per-day cap across restarts; move to persistent storage before authorizing paid API use.

## October 2026 model migration and live-test safeguards

Gemini 2.5 Flash may not be accessible to newly provisioned Google AI Studio API projects. The default is now `gemini-3.5-flash-lite`. Groq retired `llama-3.3-70b-versatile` on August 16, 2026; the default is now `qwen/qwen3.8-27b`. You may override defaults explicitly with `GEMINI_MODEL` and `GROQ_MODEL`. Metadata-only checks show whether a selected model appears in the provider catalog, but are not proof that inference succeeds or meets budget/quota.

OpenAI HTTP 429 can mean prepaid credit, project/organization spend caps, or a temporary rate limit. This integration never purchases API credits or enables ongoing requests automatically. Check the API organization billing and limit settings separately from your ChatGPT subscription. Production AI requests remain off by default; only a separately approved, bounded diagnostic can contact real models without enabling the UI's live AI option.

## Official source data: SEC, FDA and FRED

Nexus Data sources offers read-only research from three official hosts, separate from AI model requests and brokerage.

- SEC EDGAR: public SEC company ticker list and data.sec.gov submissions/CIK##########.json. No API key. Supply SEC_USER_AGENT with a genuine contact string for fair access; the application has a descriptive fallback. Filing metadata is not a verified investment conclusion.
- FDA openFDA: official drug and device enforcement recall datasets and Drugs@FDA application data. Public keyless usage is rate limited (usually 1,000 daily per IP); an optional OPENFDA_API_KEY raises limits. Recall records do not establish clinical causation.
- FRED: official FRED API observations when FRED_API_KEY is present. Otherwise Nexus attempts the public FRED graph CSV for a fixed list of indicators. The official JSON API requires a registered free key; the CSV fallback may fail from some hosting networks. FRED values can be revised or delayed.

Source requests require the Studio access token, use fixed official hosts and strict identifiers, bound response sizes and timeouts, and cache successful results for 15 minutes. No API secrets appear in source links. A failed source is an explicit error, not an invented finding. Use these findings in a mission copies the source and its URLs to the AI composer; AI-generation permissions remain locked.

## OpenRouter Free optional fifth AI

OpenRouter Free uses OPENROUTER_API_KEY and only the free-model router openrouter/free or model IDs ending in :free. Any paid model ID is rejected before HTTP calls. Account-key verification uses OpenRouter's authenticated GET /api/v1/key metadata, not paid generation. OpenRouter's free-model quota is limited (normally 50 requests/day on a no-credit account). User account/key setup is required at https://openrouter.ai/settings/keys. Never enable paid routing, automatic credit purchases or unrestricted calls.

The default STUDIO_ALLOW_API_USAGE=false and STUDIO_DAILY_CALL_CAP=0 keep all real model requests off. This app has no brokerage order execution capabilities.

## Source deployment smoke check

Offline GitHub CI runs python -m unittest discover -s ai_studio/tests -v and node --check ai_studio/static/app.js. For an explicit operator-initiated read-only network check, set STUDIO_RUN_SOURCE_SMOKE=true temporarily, inspect the sanitized STUDIO_PUBLIC_SOURCES log, then reset it to false. No model inference, emails, or brokerage orders should occur.
