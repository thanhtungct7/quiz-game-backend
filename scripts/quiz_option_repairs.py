"""Questions whose options the source files repeat, and what to do about them.

A third defect of the extracted sources, next to the two in quiz_text_cleanup:
a question offers the same option twice. One of its distractors was lost in
extraction and the remaining text was duplicated to fill the slot. The learner
then sees two identical choices, and where the repeated text *is* the answer
(mc4:14 "cost", "cost", "costed") picking the other copy is marked wrong.

Two kinds, so two remedies:

* `OPTION_REPAIRS` -- the question still works; only one distractor is missing.
  The duplicate slot is given a distractor written for that question, wrong for
  a reason a learner can see. The answer itself is never touched: a repair that
  lands on the correct option raises rather than rewriting the answer key.
* `DROPPED_QUESTIONS` -- nothing to repair. Extraction left every option a bare
  "." or "_", or reduced the correct answer to a fragment ("Europe" as a whole
  trade route), so no set of distractors makes the question answerable. These
  are skipped at import, and scripts/drop_broken_questions.py removes the ones
  an earlier import already wrote.

Positions are 1-based, counting the source's own `options_map` order, which is
the order the importer numbers options in -- so a position here is the stored
`ChallengeOption.order_index`.
"""

from __future__ import annotations

from typing import Any

# source_ref -> {option position: the distractor that replaces the duplicate}
OPTION_REPAIRS: dict[str, dict[int, str]] = {
    # --- quiz_multiple_choice_4options.json ---------------------------------
    # "These flats ... 400,000 dollars." (cost / cost / costed)
    "mc4:14": {1: "costs"},
    # "Are you happy? Yes, I ... ." (do / am / do)
    "mc4:1436": {3: "is"},
    # "It ... this week." (happened / happened / has happened)
    "mc4:3747": {2: "happens"},
    # "She ... all the text several hours ago."
    "mc4:3950": {3: "mustn't be translated"},
    # "I hope we can count ... you." (with / on / with)
    "mc4:4090": {3: "at"},
    # Reported speech; the repeated text is the answer, so slot 1 is rewritten.
    "mc4:4252": {1: "She said that it is mentioned they had already started working there."},
    # "... you achieve your aim?" (Weren't / Didn't / Weren't)
    "mc4:4560": {3: "Don't"},
    # "I felt there so ... I wanted to live there." -- answer is slot 1.
    "mc4:4841": {3: "comfortably"},
    # "We are ... a hurry ..." (in / on / on)
    "mc4:5967": {3: "at"},
    # "Three men ... down a rope." (are sliding / are slideing / is sliding / are slideing)
    "mc4:6813": {4: "are slided"},
    # "The new clerk reported ... duty this morning." -- answer is slot 2.
    "mc4:7180": {3: "to"},
    # "Are you ... for Delhi now?" (left / leaving / left)
    "mc4:8753": {3: "leave"},
    # "... like a cat on hot ... ." (roads / bricks / roads)
    "mc4:9245": {3: "stones"},
    # "His wife is always ... that!" (said / saying / said)
    "mc4:9627": {3: "says"},
    # "There are many historic buildings ... ." -- answer is slot 3.
    "mc4:9903": {2: "at that street in Jersey"},
    # Equilateral triangle; the repeated text is the answer, so slot 1 is rewritten.
    "mc4:9997": {1: "what has three sides of equal length"},
    # "Please state your name, age and ... ." (occupied / occupation / occupied)
    "mc4:10008": {3: "occupying"},
    # "... it is important ... it on." -- answer is slot 3.
    "mc4:10029": {2: "trying"},
    # "He has ... the conditions for entry ..." (satisfactory / satisfied / satisfactory)
    "mc4:10035": {3: "satisfying"},
    # "It's time ... home." (going / to go / going)
    "mc4:10216": {3: "go"},
    # "The audience ... for five minutes." (shouted / shouted / clapped)
    "mc4:10774": {2: "whispered"},
    # "I'll just ... the fried egg ..." (cut / taste / cut)
    "mc4:10937": {3: "burn"},
    # "The two sisters greatly ... each other." (care of / resemble / care of)
    "mc4:10945": {3: "differ from"},
    # "... gone on her ... leave." (regular / annual / regular)
    "mc4:13004": {3: "monthly"},
    # "The professor was ... out of his job ..." (wiped / eased / wiped)
    "mc4:13466": {3: "washed"},
    # "Our house is ... at the corner ..." (stood / situated / stood)
    "mc4:13767": {3: "laid"},
    # "Instead of ... about the good news ..." -- answer is slot 1.
    "mc4:14303": {3: "excite"},
    # "If you cross your fingers they ... that way." (stayed / won't stay / stayed)
    "mc4:14352": {3: "didn't stay"},
    # Reported speech, "I have been waiting" -> "had been waiting".
    "mc4:15672": {3: "John said that he has been waiting for me for such a long time."},
    # --- quiz_reading_comprehension_part*.json ------------------------------
    # "What color are Kelly's eyes?" (Blonde / Black / Blue / Black)
    "reading:11826": {4: "Brown"},
    # "Peter runs _ than Betty." (faster / slow / faster / the fastest)
    "reading:14845": {3: "more fast"},
    # "What does Tom plan to make?" (Bread. / Sandwiches. / Bread. / Butter .)
    "reading:16335": {3: "Soup."},
    # "I do my homework from _ to _ every evening."
    "reading:1647": {4: "six; nine"},
    # "How many people can't find jobs every day?"
    "reading:17351": {4: "32500"},
    # "Tony stared into space and played with a piece of bread _ ."
    "reading:20115": {2: "because he was bored with the food"},
    # "What's the best title for this passage?"
    "reading:22054": {2: "How to Choose a Musical Instrument."},
    # "Ling Tao's English teacher likes to wear _ clothes."
    "reading:22893": {4: "green"},
    # "Jane is Linda's _ ." (sister / mother / friend / mother)
    "reading:26844": {4: "teacher"},
    # "Who doesn't like the soup for lunch?"
    "reading:28145": {2: "His father"},
    # "Earth Day is _ ." -- both "on 2" and "on 22" are doubled.
    "reading:2824": {3: "on 12", 4: "on 20"},
    # "What gave Rosemary the idea that she had hurt Gordon's feelings deeply?"
    "reading:29507": {3: "He refused to look at her."},
    # "Pruning should be done to _ ."
    "reading:31623": {4: "make the fruit taste sweeter"},
    # "When Ali told what he wanted to do Nasreddin was _ ."
    "reading:34313": {2: "surprised"},
    # Pet phrases; "---stunning" is doubled and keeps the article's separator.
    "reading:39415": {3: "stunning", 4: "cool"},
    # "... it might be worth _ ."
    "reading:44525": {3: "$ 100, 000"},
    # "Which facial signal can cause you to lose an opportunity of being employed?"
    "reading:48680": {3: "Looking straight into the eyes."},
    # "If the twins are easy to tell from each other, they are _ ."
    "reading:52692": {3: "certainly born on different days"},
    # "The story was an amazing coincidence because _ ."
    "reading:53511": {3: "the wallet was lost and found on the same day."},
    # "After the girl agreed to help, the author _"
    "reading:55206": {3: "paid her for the work in advance"},
    # "Mr Hunt doesn't want to buy the dress at last because _"
    "reading:5989": {3: "it is the wrong size"},
    # "What country celebrates Teachers' Day in October?"
    "reading:64677": {3: "China."},
    # "Why do people visit Venice?"
    "reading:6967": {4: "Because it is famous for its cars."},
    # "When Hank marched and drilled along with the other soldiers, he ..."
    "reading:76267": {3: "did nothing but watch the others"},
    # "Depp and Bloom sail around the Caribbean islands because _ ."
    "reading:83213": {4: "they try to find some lost silver"},
    # "They meet _ ." (terrible men / terrible women / smiling men / smiling men)
    "reading:83214": {4: "some smiling women"},
    # "What is the theme of the EXPO 2010 Shanghai China?"
    "reading:86980": {4: "Better City, Better Dream."},
    # "What happened after the man changed the sign?"
    "reading:8938": {4: "The boy took the sign away."},
    # Film festivals: three slots came out as "_". The article names the other
    # festivals' sites, so the distractors are real addresses from the passage.
    "reading:94015": {
        1: "http://www.siff.net",
        3: "www. youngcuts. com",
        4: "http:// www. Scenefirstfestival. com",
    },
    # "_ made a hole in the coat." (Mr. Jones / Mr. Jones / Mr. And Mrs. Jones / A cigarette)
    "reading:9755": {2: "Mrs. Jones"},
}

# Questions extraction left with nothing to ask. Skipped at import.
DROPPED_QUESTIONS: frozenset[str] = frozenset(
    {
        # Every option is a bare punctuation mark or placeholder: the Chinese
        # glosses and the numbers they stood for never made it into the file.
        "reading:11447",
        "reading:12211",
        "reading:1209",
        "reading:14370",
        "reading:15155",
        "reading:15415",
        "reading:20906",
        "reading:23664",
        "reading:23760",
        "reading:24128",
        "reading:24513",
        "reading:2947",
        "reading:33296",
        "reading:3444",
        "reading:4603",
        "reading:6399",
        "reading:6623",
        "reading:71878",
        "reading:78762",
        "reading:8169",
        "reading:82964",
        # The correct answer itself came out as a placeholder or a fragment, so
        # no distractor can make these answerable:
        # the website for Agra, and the resort's site, both reduced to "_";
        "reading:51442",
        "reading:87368",
        # "Europe" alone offered as a whole trade route;
        "reading:56873",
        # "UltraTouch" alone offered as a TRUE statement about the ad.
        "reading:85103",
        # Single-choice questions whose own explanation gives two right answers
        # ("to make/give a speech"; "it's no good/it's no use + -ing"), and one
        # whose two options both end in "Fail.".
        "explanations.mcq:1487",
        "explanations.mcq:1551",
        "explanations.mcq:1561",
    }
)

# Questions the source leaves with no correct option at all, because its
# `correct_answer` names no key of `options_map`: it carries the answer's text
# ("off"), or a key that was never written ("c" where the options are a and b),
# or a note from whoever transcribed it ("2/4"). The importer then marks every
# option wrong and the learner cannot answer -- `grading.answer_key` calls that
# a challenge which "must not be served".
#
# These are the ones where the intended answer is beyond doubt; the rest are in
# DROPPED_QUESTIONS. The value is the 1-based position that is correct.
ANSWER_KEY_FIXES: dict[str, int] = {
    # correct_answer "off" is option B's text; the explanation says
    # "'Break off' means 'end a relationship'".
    "explanations.mcq:1200": 2,
    # correct_answer "c" but the row has only options a and b; its own
    # correct_text "–" is option b, and matches the solved sentence.
    "mc4:2778": 2,
}


class RepairedTheAnswerError(RuntimeError):
    """A repair landed on the correct option -- it would rewrite the answer key."""


def repair_options(source_ref: str, options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace this question's duplicated distractors, in place.

    Returns `options` so it can be used inline. A question with no repair is
    left exactly as it came.
    """
    repairs = OPTION_REPAIRS.get(source_ref)
    if not repairs:
        return options
    for position, text in repairs.items():
        option = options[position - 1]
        if option["correct"]:
            raise RepairedTheAnswerError(
                f"{source_ref} option {position} is the correct answer"
            )
        option["text"] = text
    return options


def apply_answer_key(source_ref: str, options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark the intended answer on a question the source left with none.

    Only touches a question listed in ANSWER_KEY_FIXES, and only one whose
    options really are all wrong -- if a later source file marks an answer
    itself, the fix steps aside rather than moving the answer somewhere else.
    """
    position = ANSWER_KEY_FIXES.get(source_ref)
    if position is None or any(option["correct"] for option in options):
        return options
    for index, option in enumerate(options, start=1):
        option["correct"] = index == position
    return options
