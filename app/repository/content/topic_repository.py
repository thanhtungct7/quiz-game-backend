from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content.topic import Topic


class TopicRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, topic: Topic) -> Topic:
        self.db.add(topic)
        await self.db.commit()
        await self.db.refresh(topic)
        return topic

    async def list_all(self) -> list[Topic]:
        result = await self.db.execute(select(Topic).order_by(Topic.name))
        return list(result.scalars().all())

    async def get_by_id(self, topic_id: str) -> Topic | None:
        return await self.db.get(Topic, topic_id)

    async def get_by_name(self, name: str) -> Topic | None:
        result = await self.db.execute(select(Topic).where(Topic.name == name))
        return result.scalar_one_or_none()

    async def update(self, topic: Topic, data: dict[str, object]) -> Topic:
        for field, value in data.items():
            setattr(topic, field, value)
        await self.db.commit()
        await self.db.refresh(topic)
        return topic

    async def delete(self, topic: Topic) -> None:
        await self.db.delete(topic)
        await self.db.commit()
