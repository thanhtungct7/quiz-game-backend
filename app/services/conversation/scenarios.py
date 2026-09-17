"""The everyday situations a learner can practise a conversation in.

Kept in code, like `services/game/catalog.py`: there are few of them, they
change with a deploy, and the text below is also prompt material. Adding one is
adding an entry to `SCENARIOS`.

`setting`, `ai_role`, `user_role` and `goal` are English because they go into
the prompt; the `_vi` fields are what the app shows.
"""

from dataclasses import dataclass

from app.services.game.cefr import CefrBand

DEFAULT_MAX_TURNS = 12


@dataclass(frozen=True)
class Scenario:
    code: str
    title_vi: str
    category_vi: str
    # Shown as a hint only. The AI always talks at the learner's own band.
    suggested_levels: tuple[CefrBand, ...]
    setting: str
    ai_role: str
    user_role: str
    # None for open conversation: nothing to complete, so the AI never ends it.
    goal: str | None
    goal_vi: str | None
    opening_line: str
    useful_phrases: tuple[str, ...]
    max_turns: int = DEFAULT_MAX_TURNS


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        code="cafe_order",
        title_vi="Gọi đồ ở quán cà phê",
        category_vi="Ăn uống",
        suggested_levels=(CefrBand.A1, CefrBand.A2),
        setting="A small, busy coffee shop in London on a weekday morning.",
        ai_role="a friendly barista taking orders at the counter",
        user_role="a customer",
        goal="Order a drink and something to eat, then pay.",
        goal_vi="Gọi một đồ uống và một món ăn, rồi thanh toán.",
        opening_line="Hi there! What can I get for you today?",
        useful_phrases=("I'd like a...", "Can I have...", "How much is it?", "For here, please."),
    ),
    Scenario(
        code="ask_directions",
        title_vi="Hỏi đường",
        category_vi="Đi lại",
        suggested_levels=(CefrBand.A1, CefrBand.A2),
        setting="A street corner in a city centre. The learner is lost.",
        ai_role="a local person walking past who knows the area well",
        user_role="a visitor who needs to get to the train station",
        goal="Find out how to get to the train station and how long it takes.",
        goal_vi="Hỏi được đường tới ga tàu và mất bao lâu.",
        opening_line="Oh, hello. You look a bit lost. Can I help you?",
        useful_phrases=(
            "Excuse me, where is...?",
            "How do I get to...?",
            "Is it far from here?",
            "Could you say that again?",
        ),
    ),
    Scenario(
        code="clothes_shopping",
        title_vi="Mua quần áo",
        category_vi="Mua sắm",
        suggested_levels=(CefrBand.A2,),
        setting="A clothes shop in a shopping centre.",
        ai_role="a helpful shop assistant",
        user_role="a customer looking for a jacket",
        goal="Ask for a size, ask to try it on, and ask about the return policy.",
        goal_vi="Hỏi size, xin thử đồ, và hỏi chính sách đổi trả.",
        opening_line="Good afternoon! Are you looking for anything in particular?",
        useful_phrases=(
            "Do you have this in a medium?",
            "Can I try it on?",
            "Where is the fitting room?",
            "Can I return it if it doesn't fit?",
        ),
    ),
    Scenario(
        code="hotel_checkin",
        title_vi="Nhận phòng khách sạn",
        category_vi="Du lịch",
        suggested_levels=(CefrBand.A2, CefrBand.B1),
        setting=(
            "The reception desk of a mid-range hotel in the evening. Later, the learner "
            "finds that something in the room is not working."
        ),
        ai_role="a polite hotel receptionist",
        user_role="a guest with a booking",
        goal="Check in, then report one problem with the room and get it solved.",
        goal_vi="Làm thủ tục nhận phòng, rồi báo một vấn đề trong phòng và được giải quyết.",
        opening_line="Good evening and welcome. Do you have a reservation with us?",
        useful_phrases=(
            "I have a booking under the name...",
            "What time is breakfast?",
            "The air conditioning isn't working.",
            "Could someone take a look?",
        ),
    ),
    Scenario(
        code="doctor_visit",
        title_vi="Đi khám bệnh",
        category_vi="Sức khỏe",
        suggested_levels=(CefrBand.B1,),
        setting="A doctor's consulting room at a local clinic.",
        ai_role="a calm, kind family doctor",
        user_role="a patient who has not been feeling well for a few days",
        goal="Describe the symptoms and understand how to take the medicine.",
        goal_vi="Mô tả triệu chứng và hiểu cách dùng thuốc.",
        opening_line="Hello, please have a seat. So, what seems to be the problem?",
        useful_phrases=(
            "I've had a headache for three days.",
            "It hurts when I...",
            "How often should I take it?",
            "Should I come back if it gets worse?",
        ),
    ),
    Scenario(
        code="new_classmate",
        title_vi="Làm quen bạn mới",
        category_vi="Làm quen",
        suggested_levels=(CefrBand.A2, CefrBand.B1),
        setting="The first day of an English course. Two students sit next to each other.",
        ai_role="a friendly international student in the same class",
        user_role="a new student",
        goal="Introduce yourself, find a shared interest, and make a plan to meet.",
        goal_vi="Giới thiệu bản thân, tìm sở thích chung, và hẹn gặp nhau.",
        opening_line="Hi! Is this seat free? I'm Sam, by the way. Is this your first day too?",
        useful_phrases=(
            "Nice to meet you.",
            "Where are you from?",
            "What do you do in your free time?",
            "Do you want to grab a coffee after class?",
        ),
    ),
    Scenario(
        code="job_interview",
        title_vi="Phỏng vấn xin việc",
        category_vi="Công việc",
        suggested_levels=(CefrBand.B1, CefrBand.B2),
        setting="A job interview for a part-time position at an international company.",
        ai_role=(
            "a professional but friendly interviewer who asks one interview question at a time"
        ),
        user_role="a candidate applying for the job",
        goal="Answer four or five common interview questions and ask one question back.",
        goal_vi="Trả lời 4–5 câu hỏi phỏng vấn thường gặp và hỏi lại 1 câu.",
        opening_line=(
            "Thanks for coming in today. To start, could you tell me a little about yourself?"
        ),
        useful_phrases=(
            "I'm currently studying...",
            "One of my strengths is...",
            "I'm interested in this job because...",
            "Could you tell me more about...?",
        ),
    ),
    Scenario(
        code="free_talk",
        title_vi="Trò chuyện với bạn nước ngoài",
        category_vi="Trò chuyện tự do",
        suggested_levels=(),
        setting=(
            "A relaxed video call between two friends. Talk about everyday life: "
            "school, work, food, hobbies, travel, weekend plans, family."
        ),
        ai_role="Emma, a friendly British friend who is curious about life in Vietnam",
        user_role="Emma's friend",
        goal=None,
        goal_vi=None,
        opening_line="Hey! It's so nice to talk to you again. How's your week been so far?",
        useful_phrases=(
            "It's been pretty busy.",
            "What about you?",
            "Last weekend I...",
            "In Vietnam, we usually...",
        ),
        max_turns=15,
    ),
)

_BY_CODE = {scenario.code: scenario for scenario in SCENARIOS}


def get_scenario(code: str) -> Scenario | None:
    return _BY_CODE.get(code)
