from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation.conversation_message import ConversationMessage, MessageRole
from app.models.conversation.conversation_session import ConversationSession


class ConversationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_session(
        self, session: ConversationSession, opening: ConversationMessage
    ) -> ConversationSession:
        self.db.add(session)
        await self.db.flush()
        opening.session_id = session.id
        self.db.add(opening)
        await self.db.commit()
        return session

    async def get_session(self, user_id: str, session_id: str) -> ConversationSession | None:
        """Only the caller's own: someone else's conversation reads as missing."""
        statement = select(ConversationSession).where(
            ConversationSession.id == session_id, ConversationSession.user_id == user_id
        )
        return (await self.db.execute(statement)).scalar_one_or_none()

    async def list_sessions(
        self, user_id: str, *, limit: int, before: datetime | None
    ) -> list[ConversationSession]:
        """Newest first. `before` is the `started_at` of the last row already shown."""
        statement = (
            select(ConversationSession)
            .where(ConversationSession.user_id == user_id)
            .order_by(ConversationSession.started_at.desc())
            .limit(limit)
        )
        if before is not None:
            statement = statement.where(ConversationSession.started_at < before)
        return list((await self.db.execute(statement)).scalars())

    async def count_started_since(self, user_id: str, since: datetime) -> int:
        statement = select(func.count()).where(
            ConversationSession.user_id == user_id, ConversationSession.started_at >= since
        )
        return int((await self.db.execute(statement)).scalar_one())

    async def messages(self, session_id: str) -> list[ConversationMessage]:
        statement = (
            select(ConversationMessage)
            .where(ConversationMessage.session_id == session_id)
            .order_by(ConversationMessage.seq)
        )
        return list((await self.db.execute(statement)).scalars())

    async def get_message(self, session_id: str, message_id: str) -> ConversationMessage | None:
        statement = select(ConversationMessage).where(
            ConversationMessage.id == message_id, ConversationMessage.session_id == session_id
        )
        return (await self.db.execute(statement)).scalar_one_or_none()

    async def add_message(
        self, session: ConversationSession, role: MessageRole, content: str, seq: int
    ) -> ConversationMessage:
        """Append one line and commit it together with whatever changed on `session`.

        Committed on its own so a learner's line survives the AI failing on the
        reply to it. `(session_id, seq)` is unique, so two racing sends cannot
        both land on the same position.
        """
        message = ConversationMessage(
            session_id=session.id,
            seq=seq,
            role=role,
            content=content,
            # Set here rather than left to the server default, which would need a
            # reload after the commit before the response could read it.
            created_at=datetime.now(UTC),
        )
        self.db.add(message)
        await self.db.commit()
        return message

    async def save(self) -> None:
        await self.db.commit()

    async def delete_session(self, session: ConversationSession) -> None:
        await self.db.execute(
            delete(ConversationSession).where(ConversationSession.id == session.id)
        )
        await self.db.commit()
