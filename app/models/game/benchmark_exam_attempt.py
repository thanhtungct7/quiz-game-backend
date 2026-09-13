from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import ARRAY, DateTime, Enum, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BenchmarkAttemptStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    PASSED = "PASSED"
    FAILED = "FAILED"
    # Replaced by a fresh sitting before it was handed in. Never graded.
    ABANDONED = "ABANDONED"


class BenchmarkExamAttempt(Base):
    """One sitting of the Benchmark Exam bound to one chốt chặn năng lực.

    The paper is drawn, held and graded here, never on the client: the client
    only ever sees the public questions and learns nothing about correctness
    until the paper is handed in. `question_ids` is the paper in the order it
    was served; `answers` maps a challenge id to what was submitted and whether
    that was right.

    At most one sitting per player is IN_PROGRESS -- the partial unique index
    is what holds that, not the service.
    """

    __tablename__ = "benchmark_exam_attempts"
    __table_args__ = (
        Index(
            "uq_benchmark_exam_attempts_one_in_progress",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'IN_PROGRESS'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cap_level: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[BenchmarkAttemptStatus] = mapped_column(
        Enum(BenchmarkAttemptStatus, name="benchmark_attempt_status"),
        nullable=False,
        default=BenchmarkAttemptStatus.IN_PROGRESS,
        server_default=BenchmarkAttemptStatus.IN_PROGRESS.value,
    )
    question_ids: Mapped[list[str]] = mapped_column(ARRAY(String(36)), nullable=False)
    # {challenge_id: {"submitted": [option ids], "correct": bool}}. Replaced
    # whole on every write: SQLAlchemy does not see a JSONB mutated in place.
    answers: Mapped[dict[str, dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    correct_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The bar in force when the paper was drawn, so changing it later never
    # regrades a sitting that already happened.
    pass_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
