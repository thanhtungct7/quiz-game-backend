"""The two statements behind an aggregated profile.

There is no database in this suite, so these compile the real statements
against the PostgreSQL dialect instead. That will not catch a wrong answer,
but it does catch the things that are otherwise only found at runtime: a
misspelled column, a join that cannot be rendered, an aggregate the dialect
will not accept.
"""

from typing import Any

from sqlalchemy.dialects import postgresql

from app.repository.profile.profile_stats_repository import ProfileStatsRepository


class FakeResult:
    def __init__(self, row: Any) -> None:
        self.row = row

    def first(self) -> Any:
        return self.row

    def one(self) -> Any:
        return self.row


class RecordingSession:
    """Captures the statements a repository builds without executing them."""

    def __init__(self, row: Any) -> None:
        self.row = row
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement)
        return FakeResult(self.row)


def _sql(statement: Any) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


# --- identity + standing ---------------------------------------------------


async def test_the_identity_query_is_one_statement() -> None:
    session = RecordingSession(row=None)

    await ProfileStatsRepository(session).identity_with_standing("user-1")  # type: ignore[arg-type]

    assert len(session.statements) == 1


async def test_the_identity_query_outer_joins_both_lazy_tables() -> None:
    """Inner joins here would hide every account that has not played yet."""
    session = RecordingSession(row=None)

    await ProfileStatsRepository(session).identity_with_standing("user-1")  # type: ignore[arg-type]
    sql = _sql(session.statements[0])

    assert sql.count("LEFT OUTER JOIN") == 2
    assert "user_game_profiles" in sql
    assert "duo_ratings" in sql


async def test_a_missing_user_reads_as_none() -> None:
    session = RecordingSession(row=None)

    found = await ProfileStatsRepository(session).identity_with_standing("nobody")  # type: ignore[arg-type]

    assert found is None


async def test_the_three_rows_come_back_in_order() -> None:
    session = RecordingSession(row=("the-user", "the-profile", "the-rating"))

    found = await ProfileStatsRepository(session).identity_with_standing("user-1")  # type: ignore[arg-type]

    assert found == ("the-user", "the-profile", "the-rating")


# --- learning totals -------------------------------------------------------


async def test_the_totals_query_is_one_statement() -> None:
    session = RecordingSession(row=(0, 0, 0))

    await ProfileStatsRepository(session).learning_totals("user-1")  # type: ignore[arg-type]

    assert len(session.statements) == 1


async def test_the_totals_query_counts_mastered_in_the_same_pass() -> None:
    session = RecordingSession(row=(0, 0, 0))

    await ProfileStatsRepository(session).learning_totals("user-1")  # type: ignore[arg-type]
    sql = _sql(session.statements[0])

    assert "FILTER (WHERE" in sql
    assert "coalesce" in sql.lower()
    assert "user_challenge_progress" in sql


async def test_the_totals_come_back_as_plain_ints() -> None:
    """`sum()` answers a Decimal on PostgreSQL; the schema wants an int."""
    session = RecordingSession(row=(40, 30, 60))

    totals = await ProfileStatsRepository(session).learning_totals("user-1")  # type: ignore[arg-type]

    assert totals == (40, 30, 60)
    assert all(type(value) is int for value in totals)
