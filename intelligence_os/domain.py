"""Typed, provider-neutral research contracts."""
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

class JobState(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    partial = "partial"
    failed = "failed"

class Evidence(BaseModel):
    source_id: str
    url: str
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    observed_at: datetime | None = None
    excerpt: str = Field(max_length=6000)
    verified: bool = False

class AgentOutput(BaseModel):
    role: str
    provider: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)

class ResearchResult(BaseModel):
    question: str
    state: JobState
    outputs: list[AgentOutput] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    brokerage_execution_enabled: bool = False
