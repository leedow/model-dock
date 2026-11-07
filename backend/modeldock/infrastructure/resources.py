from typing import Any, Protocol


class ResourceProvider(Protocol):
    def snapshot(self, allocated_mb: int) -> dict[str, Any]: ...


class MockResourceProvider:
    """Deliberately virtual: never claims to inspect the host's actual GPU."""

    def __init__(self, total_mb: int):
        self.total_mb = total_mb

    def snapshot(self, allocated_mb: int) -> dict[str, Any]:
        return {
            "simulated": True,
            "devices": [
                {
                    "id": "virtual:0",
                    "name": "ModelDock Virtual GPU",
                    "total_mb": self.total_mb,
                    "used_mb": allocated_mb,
                    "free_mb": max(0, self.total_mb - allocated_mb),
                }
            ],
        }
