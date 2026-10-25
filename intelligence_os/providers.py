"""Adapter contract. No external calls occur in this module."""
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass(frozen=True)
class ProviderPolicy:
    timeout_seconds: int = 45
    max_output_tokens: int = 1500
    paid_calls_allowed: bool = False

class ProviderError(RuntimeError):
    pass

class ProviderAdapter(ABC):
    name: str
    @abstractmethod
    async def complete(self, *, system: str, prompt: str, policy: ProviderPolicy) -> str:
        """Return text or raise ProviderError. Never expose credentials."""
        raise NotImplementedError

class DisabledProvider(ProviderAdapter):
    name = "disabled"
    async def complete(self, *, system: str, prompt: str, policy: ProviderPolicy) -> str:
        raise ProviderError("No inference provider has been configured")
