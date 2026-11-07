"""Extension templates: intentionally not executable until a concrete engine is installed."""

from modeldock.adapters.base import BaseAdapter
from modeldock.errors import DockError


class LocalModelTemplate(BaseAdapter):
    async def load(self) -> None:
        raise DockError("Implement local model loading in a concrete Adapter", "adapter_not_implemented")


class RemoteServiceTemplate(BaseAdapter):
    async def load(self) -> None:
        raise DockError(
            "Implement remote protocol translation in a concrete Adapter", "adapter_not_implemented"
        )
