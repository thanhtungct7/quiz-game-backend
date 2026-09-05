"""Turning a stored `Challenge` into the learner-facing view.

There is one rule here that the plain `model_validate` cannot honour: an ORDER
challenge keeps its answer in `ChallengeOption.order_index`, and the options
relationship is loaded *sorted by that column*. Serving those options as they
come out of the database hands the learner the finished sentence, and
`order_index` itself spells it out even if the list is shuffled. So ORDER
options are always shuffled and then renumbered by display position.
"""

import random

from app.models.content.challenge import Challenge, ChallengeType
from app.schemas.content.course_content import (
    ChallengeOptionPublicRead,
    ChallengePublicRead,
    PassageRead,
)


def to_public_challenge(
    challenge: Challenge, rng: random.Random | None = None
) -> ChallengePublicRead:
    """Client-facing view of a challenge, never carrying the answer.

    `rng` shuffles the options of every challenge type; without one only ORDER
    challenges are shuffled, seeded off the challenge id so the same challenge
    lays its tiles out the same way on every fetch (a re-fetch mid-lesson must
    not reshuffle the word bank under the learner's thumb).
    """
    options = list(challenge.options)
    if challenge.type is ChallengeType.ORDER:
        (rng or random.Random(challenge.id)).shuffle(options)  # noqa: S311 -- laying out tiles
        public_options = [
            ChallengeOptionPublicRead(
                id=option.id,
                text=option.text,
                # Display position, not the stored one: the stored index *is*
                # the answer key.
                order_index=position,
                image_src=option.image_src,
                audio_src=option.audio_src,
            )
            for position, option in enumerate(options, start=1)
        ]
    else:
        if rng is not None:
            rng.shuffle(options)
        public_options = [ChallengeOptionPublicRead.model_validate(o) for o in options]

    return ChallengePublicRead(
        id=challenge.id,
        lesson_id=challenge.lesson_id,
        type=challenge.type,
        question=challenge.question,
        difficulty=challenge.difficulty,
        topic_id=challenge.topic_id,
        order_index=challenge.order_index,
        passage=PassageRead.model_validate(challenge.passage) if challenge.passage else None,
        options=public_options,
    )
