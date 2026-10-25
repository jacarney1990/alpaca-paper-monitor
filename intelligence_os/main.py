"""Independent Intelligence OS API foundation."""
from datetime import datetime, timezone
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="Intelligence OS", version="0.1.0", docs_url=None, redoc_url=None)

ROLES = ("Collector", "Analyst", "Validator", "Synthesizer")

class ResearchPlanRequest(BaseModel):
    question: str = Field(min_length=3, max_length=4000)

@app.get("/health")
def health():
    return {"status": "ok", "service": "intelligence-os", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/capabilities")
def capabilities():
    return {"roles": ROLES, "provider_calls_enabled": False,
            "brokerage_execution_enabled": False, "research_execution_enabled": False}

@app.post("/research/plan")
def plan_research(payload: ResearchPlanRequest):
    return {"question": payload.question, "execution": "not_started",
            "stages": [{"role": role, "status": "planned"} for role in ROLES],
            "safety": {"brokerage_execution": "disabled", "paid_api_calls": "disabled"}}
