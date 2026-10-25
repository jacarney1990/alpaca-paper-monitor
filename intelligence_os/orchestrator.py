"""Deterministic role ordering; no orders or side effects beyond provider calls."""
from .domain import AgentOutput, JobState, ResearchResult
from .providers import ProviderAdapter, ProviderError, ProviderPolicy

ROLES = ("Collector", "Analyst", "Validator", "Synthesizer")

async def execute_research(question: str, provider: ProviderAdapter, *,
                           policy: ProviderPolicy | None = None) -> ResearchResult:
    policy = policy or ProviderPolicy()
    outputs: list[AgentOutput] = []
    warnings: list[str] = []
    for role in ROLES:
        context = "\n\n".join(f"{item.role}: {item.text}" for item in outputs)
        instruction = {
            "Collector": "Gather evidence. Do not invent sources.",
            "Analyst": "Analyze the evidence and identify uncertainty.",
            "Validator": "Challenge unsupported claims, arithmetic and dates.",
            "Synthesizer": "Produce a careful final answer with limitations.",
        }[role]
        try:
            response = await provider.complete(
                system=f"You are the {role}. {instruction}",
                prompt=f"Question: {question}\nPrior team outputs:\n{context[:12000]}",
                policy=policy)
            if not response.strip():
                raise ProviderError("Empty response")
            outputs.append(AgentOutput(role=role, provider=provider.name, text=response[:16000]))
        except ProviderError:
            warnings.append(f"{role} could not complete")
            break
    state = JobState.completed if len(outputs) == len(ROLES) else JobState.partial if outputs else JobState.failed
    return ResearchResult(question=question, state=state, outputs=outputs, warnings=warnings)
