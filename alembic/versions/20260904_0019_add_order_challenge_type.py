"""Add the ORDER challenge type and move the imported sentence builders onto it.

"Ghép câu" questions were imported as ASSIST with every word flagged correct,
which made them unanswerable-wrong: the check endpoint grades a single tapped
option against its `correct` flag, so every tile scored. They are really a
word-ordering challenge whose answer is the option `order_index` sequence --
which the importer already writes -- so no option rows have to change, only
the challenge's type.

`ALTER TYPE ... ADD VALUE` cannot be used in the same transaction that adds
it, hence the autocommit block around it.

Revision ID: 20260904_0019
Revises: 20260830_0018
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260904_0019"
down_revision: str | None = "20260830_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE challenge_type ADD VALUE IF NOT EXISTS 'ORDER'")

    # `source_ref` is what the importer stamps each row with, so it identifies
    # the sentence builders exactly -- no guessing from option shape.
    op.execute(
        """
        UPDATE challenges
        SET type = 'ORDER'
        WHERE type = 'ASSIST' AND source_ref LIKE 'sentence_builder:%'
        """
    )

    # These rows were written with a prompt that asks for a translation, which
    # is not what the screen now offers -- and with no explanation, so a wrong
    # answer taught nothing. Both are rebuilt here rather than left to wait for
    # a re-import of ~156k challenges. `correct_text` is the assembled sentence
    # the importer already stored; it carries no end punctuation, which the
    # re-import's `vietnamese` field would, and that is the only difference
    # between a migrated row and a freshly imported one.
    op.execute(
        """
        UPDATE challenges
        SET question = replace(
                question,
                'Dịch câu sau sang tiếng Việt: ',
                'Sắp xếp các từ thành câu dịch đúng của: '
            ),
            explanation = coalesce(explanation, 'Câu đúng: ' || correct_text)
        WHERE type = 'ORDER' AND source_ref LIKE 'sentence_builder:%'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE challenges
        SET question = replace(
            question,
            'Sắp xếp các từ thành câu dịch đúng của: ',
            'Dịch câu sau sang tiếng Việt: '
        )
        WHERE type = 'ORDER' AND source_ref LIKE 'sentence_builder:%'
        """
    )
    # The rebuilt explanation is left in place: it is true of the challenge
    # either way, and there is no record of which rows had none before.
    # The enum value stays too -- Postgres cannot drop one, and leaving it
    # costs nothing once no row uses it.
    op.execute("UPDATE challenges SET type = 'ASSIST' WHERE type = 'ORDER'")
