"""Repairs for text the quiz source files carry broken.

Two defects, both left behind by however the files were extracted:

* A word's last letter split off by a space -- "hav e", "readin g", "the y" --
  throughout quiz_multiple_choice_4options.json and, now and then, in the
  reading comprehension questions and articles.
* The multiple-choice `solved_sentence`, built by dropping the answer into the
  blank of "is ... .", keeps the space in front of the full stop: "is hers .".

scripts/import_quiz_bank.py cleans what it imports through here, and
scripts/fix_quiz_text.py repairs an already-imported database through the same
functions, so the two can never disagree about what clean text is.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

OXFORD_WORDS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "resources" / "oxford_5000_words.json"
)

# A lone letter after a word: "hav e". Never "a" or "I", which are words of their
# own, and not when a letter, apostrophe or hyphen follows ("her e-mail") or a
# dot and a letter do ("her e.mail"). Nor after an apostrophe: in "It's o n" the
# split word is "o n", and taking the "s" of the contraction would make "It'so n".
_SPLIT_LAST_LETTER = re.compile(r"(?<!['’])\b([A-Za-z]+) ([b-hj-z])\b(?![\w'’-]|\.\w)")

# Joins that rule would make and that are wrong in this data. The space there is
# a lost apostrophe ("It s", "I d be safe"), an article or an abbreviation ("a d
# discussion", "900 a d", "a b c d"), or a space that slipped by one letter
# ("be t he", "the n ew", "To p raise", "u to o", "star e f TV", "World War n").
_NEVER_JOIN = frozenset(
    {"ad", "as", "bet", "cd", "id", "its", "stare", "then", "too", "top", "warn"}
)

# Only before a mark that ends a clause, so an ellipsis (" ...") is left alone.
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([.,!?;:])(?=\s|$)")

# "What is the bad thing about the car?." -- the extractor appended the
# sentence's full stop to a mark that had already ended it. Anchored at the end
# of the text: every one of these in the corpus is final, and anchoring is what
# keeps a blank written "... ." and a mid-sentence "?" out of reach.
_DOUBLED_END_MARK = re.compile(r"([?!])\.\s*$")

# Faults no rule can find, keyed by the challenge's source_ref. Applied before
# the automatic pass.
MANUAL_FIXES: dict[str, tuple[tuple[str, str], ...]] = {
    # A typo in the source, in the question and the solved sentence alike.
    "mc4:1671": (("They money", "The money"),),
    # Its distractors are "have" and "hav e": rejoined, the question would offer
    # the same option twice.
    "mc4:1535": (("hav e", "do"),),
    # Questions carrying a separator the extraction left behind. "---" is not
    # swept up by a rule: elsewhere in this corpus it is a real separator, in
    # an option ("wet hay ---- becomes dry ---- gives off heat") and in a
    # headline ("My father---my first and lifelong English teacher").
    "reading:6054": (("---What does Nick buy for Mary?  ---  _", "What does Nick buy for Mary?"),),
    "reading:27473": (
        ("snooker  ---When Ding Junhui was young.", "snooker  _  when Ding Junhui was young."),
    ),
    "reading:18622": (
        ("Whom  is the old man living with ?.", "Whom is the old man living with?"),
    ),
}


@cache
def _dictionary() -> frozenset[str]:
    entries = json.loads(OXFORD_WORDS_PATH.read_text(encoding="utf-8"))
    words: set[str] = set()
    for entry in entries:
        word = (entry.get("value") or {}).get("word")
        if isinstance(word, str) and word.isalpha():
            words.add(word.lower())
    return frozenset(words)


def rejoin_split_words(text: str) -> str:
    """Join a split-off last letter back on when that makes an English word."""
    dictionary = _dictionary()

    def join(match: re.Match[str]) -> str:
        word = match.group(1) + match.group(2)
        key = word.lower()
        return word if key in dictionary and key not in _NEVER_JOIN else match.group(0)

    return _SPLIT_LAST_LETTER.sub(join, text)


def tidy_end_mark(text: str) -> str:
    """Drop a full stop that follows the "?" or "!" already ending the text."""
    return _DOUBLED_END_MARK.sub(r"\1", text)


def tidy_punctuation(text: str) -> str:
    """Drop the space before a clause-ending mark: "is hers ." -> "is hers.".

    For assembled sentences only. A question's blank is written "... ." and
    must keep its shape.
    """
    return _SPACE_BEFORE_PUNCTUATION.sub(r"\1", text)


def apply_manual_fixes(source_ref: str, text: str) -> str:
    for old, new in MANUAL_FIXES.get(source_ref, ()):
        text = text.replace(old, new)
    return text


def clean_text(source_ref: str, text: str) -> str:
    """A question, an option or an article.

    Manual fixes go first: one of them rewrites a split word that the automatic
    pass would otherwise have joined.
    """
    return tidy_end_mark(rejoin_split_words(apply_manual_fixes(source_ref, text)))


def clean_sentence(source_ref: str, text: str) -> str:
    """A sentence assembled from a question and its answer."""
    return tidy_punctuation(clean_text(source_ref, text))


def as_is(_source_ref: str, text: str) -> str:
    """The identity cleaner, for reading the source exactly as it was imported."""
    return text
