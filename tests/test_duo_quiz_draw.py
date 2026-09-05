import pytest

from app.models.content.challenge import Challenge, ChallengeDifficulty, ChallengeType
from app.models.content.challenge_option import ChallengeOption
from app.services.content.quiz_service import QuizService


class FakeChallengeRepository:
    def __init__(self, challenges: list[Challenge]) -> None:
        self.challenges = challenges
        self.last_limit: int | None = None

    async def list_random_filtered(
        self,
        limit: int,
        topic_ids: list[str] | None = None,
        difficulties: list[ChallengeDifficulty] | None = None,
    ) -> list[Challenge]:
        self.last_limit = limit
        pool = list(self.challenges)
        if topic_ids:
            pool = [c for c in pool if c.topic_id in topic_ids]
        if difficulties:
            pool = [c for c in pool if c.difficulty in difficulties]
        return pool[:limit]


def _make_challenge(
    challenge_id: str,
    *,
    topic_id: str | None = None,
    difficulty: ChallengeDifficulty = ChallengeDifficulty.EASY,
    with_correct_option: bool = True,
) -> Challenge:
    return Challenge(
        id=challenge_id,
        lesson_id="lesson-1",
        type=ChallengeType.SELECT,
        question=f"Question {challenge_id}",
        difficulty=difficulty,
        topic_id=topic_id,
        order_index=1,
        options=[
            ChallengeOption(
                id=f"{challenge_id}-a",
                text="A",
                correct=with_correct_option,
                order_index=1,
            ),
            ChallengeOption(id=f"{challenge_id}-b", text="B", correct=False, order_index=2),
        ],
    )


def _service(challenges: list[Challenge]) -> tuple[QuizService, FakeChallengeRepository]:
    repository = FakeChallengeRepository(challenges)
    service = QuizService(
        challenges=repository,  # type: ignore[arg-type]
        lessons=None,  # type: ignore[arg-type]
        units=None,  # type: ignore[arg-type]
    )
    return service, repository


async def test_generate_for_duo_returns_questions_and_matching_answer_key() -> None:
    service, _ = _service([_make_challenge(f"c{i}") for i in range(10)])

    result = await service.generate_for_duo(count=5)

    assert len(result.questions) == 5
    assert set(result.answer_key) == {question.id for question in result.questions}
    for question in result.questions:
        assert result.answer_key[question.id] == [f"{question.id}-a"]


async def test_generate_for_duo_hides_which_option_is_correct() -> None:
    service, _ = _service([_make_challenge("c1")])

    result = await service.generate_for_duo(count=1)

    option_fields = result.questions[0].options[0].model_dump()
    assert "correct" not in option_fields


async def test_generate_for_duo_skips_challenges_without_a_correct_option() -> None:
    challenges = [
        _make_challenge("broken-1", with_correct_option=False),
        _make_challenge("broken-2", with_correct_option=False),
        _make_challenge("good-1"),
    ]
    service, _ = _service(challenges)

    result = await service.generate_for_duo(count=3)

    assert [question.id for question in result.questions] == ["good-1"]


async def test_generate_for_duo_over_fetches_to_survive_dropped_challenges() -> None:
    service, repository = _service([_make_challenge(f"c{i}") for i in range(30)])

    await service.generate_for_duo(count=5)

    assert repository.last_limit is not None
    assert repository.last_limit > 5


async def test_generate_for_duo_applies_topic_and_difficulty_filters() -> None:
    challenges = [
        _make_challenge("easy-other", topic_id="t2"),
        _make_challenge("hard-wanted", topic_id="t1", difficulty=ChallengeDifficulty.HARD),
        _make_challenge("easy-wanted", topic_id="t1"),
    ]
    service, _ = _service(challenges)

    result = await service.generate_for_duo(
        count=5, topic_ids=["t1"], difficulties=[ChallengeDifficulty.HARD]
    )

    assert [question.id for question in result.questions] == ["hard-wanted"]


async def test_generate_for_duo_is_reproducible_for_a_given_seed() -> None:
    challenges = [_make_challenge(f"c{i}") for i in range(10)]
    service_a, _ = _service(challenges)
    service_b, _ = _service(challenges)

    first = await service_a.generate_for_duo(count=5, seed=42)
    second = await service_b.generate_for_duo(count=5, seed=42)

    assert [q.id for q in first.questions] == [q.id for q in second.questions]
    assert [o.id for o in first.questions[0].options] == [
        o.id for o in second.questions[0].options
    ]


def _make_order_challenge(challenge_id: str) -> Challenge:
    """A "ghép câu" challenge as the importer writes one: every tile correct,
    the answer carried by the option order_index run."""
    return Challenge(
        id=challenge_id,
        lesson_id="lesson-1",
        type=ChallengeType.ORDER,
        question=f"Question {challenge_id}",
        difficulty=ChallengeDifficulty.EASY,
        order_index=1,
        options=[
            ChallengeOption(id=f"{challenge_id}-1", text="Tôi", correct=True, order_index=1),
            ChallengeOption(id=f"{challenge_id}-2", text="đi", correct=True, order_index=2),
            ChallengeOption(id=f"{challenge_id}-3", text="ngủ", correct=True, order_index=3),
        ],
    )


@pytest.mark.asyncio
async def test_duo_draw_skips_order_challenges() -> None:
    """A duo round is one timed tap. An ORDER challenge cannot be answered
    that way, and since all of its tiles are flagged correct it would come out
    as a round where every tap scores."""
    service, _ = _service([_make_order_challenge("ordered"), _make_challenge("c1")])

    drawn = await service.generate_for_duo(count=5)

    assert [question.id for question in drawn.questions] == ["c1"]
