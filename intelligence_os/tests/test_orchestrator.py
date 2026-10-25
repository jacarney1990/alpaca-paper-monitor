import pytest
from intelligence_os.orchestrator import execute_research
from intelligence_os.providers import ProviderAdapter, ProviderError

class FakeProvider(ProviderAdapter):
    name = "fake"
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at
    async def complete(self, *, system, prompt, policy):
        self.calls.append((system, prompt))
        if len(self.calls) == self.fail_at:
            raise ProviderError("test failure")
        return f"answer {len(self.calls)}"

@pytest.mark.asyncio
async def test_four_roles():
    provider = FakeProvider()
    result = await execute_research("What changed?", provider)
    assert result.state == "completed"
    assert [x.role for x in result.outputs] == ["Collector", "Analyst", "Validator", "Synthesizer"]
    assert len(provider.calls) == 4
    assert result.brokerage_execution_enabled is False

@pytest.mark.asyncio
async def test_partial_failure():
    result = await execute_research("What changed?", FakeProvider(fail_at=3))
    assert result.state == "partial"
    assert len(result.outputs) == 2
