"""The layout rules of scripts/import_quiz_bank.py, checked without a database.

A lesson holds exactly ten questions of one kind, a unit cycles through every
kind its TOEIC band has, and units climb the score scale -- all three are
decided by pure functions, so they can be tested straight.
"""

from random import Random
from typing import Any

from scripts.import_quiz_bank import (
    BAND_INDEX,
    BANDS,
    KIND_COMMON_ERROR,
    KIND_MC4,
    KIND_READING,
    KIND_SENTENCE_BUILDER,
    KIND_TRUE_FALSE,
    LABEL_OF_KIND,
    TOPIC_OF_KIND,
    Band,
    KindPool,
    PathLayout,
    SourceItem,
    _cefr_rank,
    _pack_pool,
    _plan_band,
    load_json,
)

PER_LESSON = 10


def _item(kind: str, index: int, passage: str | None = None) -> SourceItem:
    fields: dict[str, Any] = {
        "question": f"{kind}-{index}",
        "toeic_band": "450-650",
        "cefr_level": "B1",
    }
    options = [{"text": "a", "correct": True}]
    passage_info = (
        None
        if passage is None
        else {"source_ref": passage, "content": "article", "level_grade": "middle"}
    )
    return fields, options, passage_info


def _packed_pool(kind: str, lessons: int, cefr: str = "B1", extra: int = 0) -> KindPool:
    pool = KindPool(
        kind=kind,
        cefr=cefr,
        items=[_item(kind, i) for i in range(lessons * PER_LESSON + extra)],
    )
    _pack_pool(pool, PER_LESSON, Random(0))  # noqa: S311 - deterministic test fixture
    return pool


# --- packing ---------------------------------------------------------------


def test_every_packed_lesson_holds_exactly_ten_questions() -> None:
    pool = _packed_pool(KIND_MC4, lessons=2, extra=5)

    assert [len(lesson) for lesson in pool.lessons] == [PER_LESSON, PER_LESSON]
    assert len(pool.leftover) == 5


def test_reading_lessons_never_split_an_article() -> None:
    # Sizes 3+3+4 and 4+4+2 both close a lesson exactly; nothing may be cut.
    blocks = {"a": 3, "b": 3, "c": 4, "d": 4, "e": 4, "f": 2}
    items = [
        _item(KIND_READING, index, passage=article)
        for article, size in blocks.items()
        for index in range(size)
    ]
    pool = KindPool(kind=KIND_READING, cefr="B2-C1", items=items)

    _pack_pool(pool, PER_LESSON, Random(7))  # noqa: S311

    assert [len(lesson) for lesson in pool.lessons] == [PER_LESSON, PER_LESSON]
    for lesson in pool.lessons:
        seen: dict[str, int] = {}
        for _fields, _options, passage in lesson:
            assert passage is not None
            seen[passage["source_ref"]] = seen.get(passage["source_ref"], 0) + 1
        # Whole articles only: every article in this lesson brought all its questions.
        assert all(count == blocks[article] for article, count in seen.items())


def test_an_article_longer_than_a_lesson_goes_to_the_bank() -> None:
    items = [_item(KIND_READING, index, passage="huge") for index in range(12)]
    pool = KindPool(kind=KIND_READING, cefr="B2-C1", items=items)

    _pack_pool(pool, PER_LESSON, Random(0))  # noqa: S311

    assert pool.lessons == []
    assert len(pool.leftover) == 12


def test_packing_is_reproducible_for_a_seed() -> None:
    def questions(seed: int) -> list[str]:
        pool = KindPool(kind=KIND_MC4, cefr="B1", items=[_item(KIND_MC4, i) for i in range(50)])
        _pack_pool(pool, PER_LESSON, Random(seed))  # noqa: S311
        return [fields["question"] for lesson in pool.lessons for fields, _, _ in lesson]

    assert questions(11) == questions(11)
    assert questions(11) != questions(12)


# --- planning --------------------------------------------------------------


def _band(key: str) -> Band:
    return next(band for band in BANDS if band.key == key)


def test_a_unit_cycles_through_every_kind_of_its_band() -> None:
    band = _band("B3")  # four kinds
    pools = {kind: _packed_pool(kind, lessons=60) for kind in band.kinds}

    plan = _plan_band(band, pools, PathLayout())

    assert plan.units == PathLayout().max_units_per_band
    assert plan.lessons_per_kind == dict.fromkeys(band.kinds, 5)
    order = plan.unit_kind_order()
    assert len(order) == PathLayout().lessons_per_unit
    assert set(order) == set(band.kinds)
    assert all(first != second for first, second in zip(order, order[1:], strict=False))


def test_an_uneven_split_still_alternates_kinds() -> None:
    band = _band("B1")  # three kinds into 20 lessons -> 7/7/6
    pools = {kind: _packed_pool(kind, lessons=60) for kind in band.kinds}

    plan = _plan_band(band, pools, PathLayout())

    assert sorted(plan.lessons_per_kind.values()) == [6, 7, 7]
    order = plan.unit_kind_order()
    assert len(order) == 20
    assert all(first != second for first, second in zip(order, order[1:], strict=False))


def test_the_scarcest_kind_caps_a_bands_units() -> None:
    band = _band("B3")
    pools = {kind: _packed_pool(kind, lessons=60) for kind in band.kinds}
    # 20 lessons of the scarcest kind, 5 needed per unit -> four units.
    pools[KIND_COMMON_ERROR] = _packed_pool(KIND_COMMON_ERROR, lessons=20)

    plan = _plan_band(band, pools, PathLayout())

    assert plan.units == 4


def test_kind_order_follows_cefr() -> None:
    band = _band("B1")
    pools = {
        KIND_MC4: _packed_pool(KIND_MC4, lessons=60, cefr="A1"),
        KIND_SENTENCE_BUILDER: _packed_pool(KIND_SENTENCE_BUILDER, lessons=60, cefr="A1-A2"),
        KIND_TRUE_FALSE: _packed_pool(KIND_TRUE_FALSE, lessons=60, cefr="A2"),
    }

    plan = _plan_band(band, pools, PathLayout())

    assert plan.kinds == (KIND_MC4, KIND_SENTENCE_BUILDER, KIND_TRUE_FALSE)
    assert _cefr_rank("A1") < _cefr_rank("A1-A2") < _cefr_rank("A2") < _cefr_rank("B2-C1")


# --- the band table --------------------------------------------------------


def test_bands_climb_the_score_scale() -> None:
    floors = [int(band.label.split("-")[0]) for band in BANDS]
    assert floors == sorted(floors)


def test_every_band_mixes_at_least_two_kinds() -> None:
    for band in BANDS:
        assert len(band.kinds) >= 2, band.label
        assert len(set(band.kinds)) == len(band.kinds), band.label


def test_every_kind_is_reachable_and_named() -> None:
    banded = {kind for band in BANDS for kind in band.kinds}
    assert banded == set(TOPIC_OF_KIND) == set(LABEL_OF_KIND)


def test_the_band_table_covers_the_source_data() -> None:
    """Guards against a source file gaining a toeic_band nothing maps.

    Only the two small files are checked -- the reading and sentence-builder
    corpora are 240 MB and would make this suite crawl.
    """
    seen = {
        (KIND_MC4, row["toeic_band"]) for row in load_json("quiz_multiple_choice_4options.json")
    }
    explanations = load_json("quiz_with_explanations.json")
    for kind, dataset in (
        ("explanations", "multiple_choice_quizzes"),
        (KIND_COMMON_ERROR, "common_error_corrections"),
        ("find_error", "grammar_rules_find_error"),
    ):
        seen |= {(kind, row["toeic_band"]) for row in explanations[dataset]}

    assert seen <= set(BAND_INDEX), sorted(seen - set(BAND_INDEX))
