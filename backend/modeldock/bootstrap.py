import asyncio

from sqlalchemy import select

from modeldock.config import get_settings
from modeldock.db import Session
from modeldock.entities import Model, User
from modeldock.security import hasher


async def initialize(sessions=Session, settings=None) -> None:
    settings = settings or get_settings()
    async with sessions() as session:
        existing = await session.scalar(select(User).where(User.role == "admin"))
        if not existing:
            if len(settings.admin_password) < 12:
                raise RuntimeError(
                    "Set MODELDock_ADMIN_PASSWORD to a unique password of at least 12 characters"
                )
            session.add(
                User(
                    username=settings.admin_username,
                    password_hash=hasher.hash(settings.admin_password),
                    role="admin",
                )
            )
        for capability, memory in [("chat", 4096), ("image", 6144), ("tts", 1024), ("asr", 2048)]:
            name = f"mock-{capability}"
            if not await session.scalar(select(Model).where(Model.name == name)):
                session.add(
                    Model(
                        name=name,
                        capability=capability,
                        adapter=f"mock.{capability}",
                        enabled=True,
                        memory_mb=memory,
                        config={"delay_ms": 80},
                    )
                )
        await session.commit()


def run() -> None:
    asyncio.run(initialize())


if __name__ == "__main__":
    run()
