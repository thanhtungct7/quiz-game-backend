# Quiz Game Backend

FastAPI starter for the Quiz Game application. It includes PostgreSQL, async SQLAlchemy,
Alembic migrations, JWT authentication, Argon2 password hashing, health checks, Docker,
tests, linting, and environment-based configuration.

## Requirements

- Conda (Miniconda, Anaconda, or Miniforge)
- PostgreSQL 16+ or Docker

## Run locally

```bash
cp .env.example .env
conda env update -n backend -f environment.yml
conda activate backend
alembic upgrade head
uvicorn app.main:app --reload
```

After changing Python dependencies, update the existing Conda environment with:

```bash
conda env update -n backend -f environment.yml
```

Open `http://127.0.0.1:8000/docs` in development.

## Run with Docker

```bash
cp .env.example .env
docker compose up --build
```

Before deploying, replace `SECRET_KEY`, database credentials, `CORS_ORIGINS`, and
`ALLOWED_HOSTS`. Configure `GOOGLE_WEB_CLIENT_ID` with the OAuth 2.0 Web application
client ID used as Android's `serverClientId`. Set `ENVIRONMENT=production`; API
documentation is then disabled.

## Initial endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/v1/health/live` | Process liveness |
| GET | `/api/v1/health/ready` | Database readiness |
| POST | `/api/v1/auth/register` | Register with email/password |
| POST | `/api/v1/auth/login` | Get an access and refresh token pair |
| POST | `/api/v1/auth/refresh` | Rotate a refresh token and get a new token pair |
| POST | `/api/v1/auth/logout` | Revoke a refresh token |
| POST | `/api/v1/auth/google` | Exchange a verified Google ID token for an app token pair |
| POST | `/api/v1/auth/forgot-password` | Email a one-time reset link (always 202) |
| POST | `/api/v1/auth/reset-password` | Consume the reset token and set a new password |
| GET | `/api/v1/users/me` | Read the authenticated user |
| GET | `/api/v1/courses/{id}/tree` | The whole learn path in one ETag-cacheable payload |
| GET | `/api/v1/lessons/{id}/challenges` | One lesson's questions (paged, `limit` ≤ 100) |
| GET | `/api/v1/progress/courses/{id}` | Your progress for every lesson of a course |
| GET | `/api/v1/profile/me` | Your whole profile in one call: identity, level, CEFR band, PvP record and study totals |
| GET | `/api/v1/profile/{user_id}` | Another player's public card, as opened from a leaderboard row or a lobby |
| GET | `/api/v1/duo/matches` | Your 1v1 match history |
| GET | `/api/v1/duo/matches/{id}` | One match with its round-by-round detail |
| GET | `/api/v1/duo/me/stats` | Your rating, W/L/D, win rate and streak |
| GET | `/api/v1/duo/leaderboard` | Top players by rating, plus your rank (`?season=current\|all_time`) |
| GET | `/api/v1/duo/rooms/{room_code}` | Preview a friend's room before joining |
| WS | `/api/v1/duo/ws` | Play a 1v1 match |
| GET | `/api/v1/game/profile` | Your level, experience, progress to the next level, gold and class |
| GET | `/api/v1/game/classes` | The three classes and their stat blocks |
| POST | `/api/v1/game/class` | Pick a class (first is free, then 500 gold) |
| GET | `/api/v1/game/skills` | The skill tree with `owned` / `unlockable` / `locked_reason` per node |
| POST | `/api/v1/game/skills/{id}/unlock` | Unlock a node you have the parent, level and gold for |
| GET/PUT | `/api/v1/game/loadout` | The three skills you take into a match |
| GET | `/api/v1/game/items` | Your inventory and the capped bonus your equipment gives |
| PUT | `/api/v1/game/equipment` | Fill or clear the weapon, armor and trinket slots |
| GET | `/api/v1/game/season/current` | The open season, your rating, tier and climb to the next |
| GET | `/api/v1/battles/monsters` | The monster catalog: name, tier, stats and art code |
| GET | `/api/v1/battles/lessons/{id}` | Which monster guards a lesson, and whether you have cleared it |
| GET | `/api/v1/battles/courses/{id}/monsters` | Every gate of a course in one request, for the map |
| GET | `/api/v1/battles/history` | Your recent lesson battles (paged) |
| WS | `/api/v1/battles/ws` | Fight the monster guarding a lesson |

Use the access token as `Authorization: Bearer <token>`.

## Password reset

`POST /auth/forgot-password` answers `202` for **every** address, whether or not it
has an account, and the app shows the same confirmation either way — anything
else turns the endpoint into an email enumeration oracle. Google-only and
inactive accounts are silently skipped for the same reason. The SMTP send runs
as a background task, so a mail provider being down cannot become a `500` that
only ever fires for addresses that do exist.

The token is one-time, expires after `PASSWORD_RESET_EXPIRE_MINUTES` (15), and
requesting a new one revokes the previous one. Consuming it revokes every refresh
token the user has: a reset is what someone does when they suspect a compromise,
so no session may survive it.

`PASSWORD_RESET_URL` is the link the email points at. The Android app registers
`quizgame://reset-password`, so the emailed link opens the reset screen on the
phone directly; the app also accepts the token pasted by hand, for a mailbox read
on a desktop. Any URL shape works — the token is appended as `?token=...`.

Development mail goes to the Mailpit service in `compose.yaml`; read it at
`http://localhost:8025`.

## Course content and the learn path

`scripts/import_quiz_bank.py` loads `data/quiz/*.json` into a
`Course → Unit → Lesson → Challenge → ChallengeOption` tree, laid out as a path
that climbs the TOEIC score scale. Three rules shape it:

- a **lesson** holds exactly ten questions, all of the same kind and the same
  source `toeic_band`;
- a **unit** holds 20 lessons of a single band, cycling through every question
  kind that band has, so two neighbouring lessons are never the same kind;
- **units run band by band, lowest band first** — walking the path is walking up
  the score scale.

The ten raw `toeic_band` values are merged into five bands (`BANDS` in the
script). Six of the ten carry only a single question kind, so without merging a
unit could not be both single-band and multi-kind:

| Unit range | Band | Question kinds |
| --- | --- | --- |
| 1-12 | TOEIC 250-450 | Điền từ · Ghép câu · Sửa lỗi Đ/S |
| 13-24 | TOEIC 350-550 | Điền từ · Đọc hiểu |
| 25-36 | TOEIC 450-700 | Điền từ · Ghép câu · Sửa lỗi Đ/S · Lỗi thường gặp |
| 37-48 | TOEIC 550-800 | Ngữ pháp có giải thích · Điền từ |
| 49-60 | TOEIC 650-990 | Đọc hiểu · Ghép câu · Sửa lỗi Đ/S · Tìm lỗi sai · Điền từ |

Reading questions are packed **by whole article** (4+3+3, 5+5, …), so a reading
lesson still lands on exactly ten without ever cutting an article in half. Which
questions reach the path is a seeded shuffle, so two imports of the same data
produce the same tree.

Everything past that budget is parked in a per-band `is_bank = true` lesson.
Bank lessons are hidden from `/courses/{id}/tree`, `/units/{id}/lessons` and
`/progress/*`, but their challenges still feed duo matches and `POST
/lessons/{id}/quiz`, which sample the whole `challenges` table. That split is
what keeps a lesson finishable: the bank holds ~144k questions, a lesson holds
ten.

```bash
python -m scripts.import_quiz_bank --reset \
  [--challenges-per-lesson 10] [--lessons-per-unit 20] \
  [--max-units-per-band 12] [--seed 20260831]
```

`GET /courses/{id}/tree` returns course, units and path lessons in one response
(~135 KB for 60 units / 1200 lessons) with an `ETag` computed from the payload.
Clients should cache it and send `If-None-Match`; an unchanged course answers
`304` with no body. It is the only content endpoint that opts out of the global
`Cache-Control: no-store`.

## 1v1 PvP (duo)

Two players answer the same question set at the same time. A correct answer is
worth 500 points plus a speed bonus of up to 500 more; a wrong or missing answer
scores nothing. **Answer time is measured on the server**, so a client cannot
claim to have answered instantly.

### Combat

Answering is also fighting. Both players start on 100 HP, and a correct answer
lands a blow on the opponent (`app/services/duo/combat.py` holds every balance
constant):

| | |
|---|---|
| Base damage | 10 at the deadline, up to 20 answering instantly |
| Quick strike | Answering within 3s adds a flat +4 |
| Heavy strike | Using 60% of the clock and still being right ignores half of any damage reduction |
| Combo | 3 correct in a row deal 1.5x; 5 in a row deal 2x, count as a critical, and stun the opponent for the next round |
| Mana | 20 (+ up to 15 for speed) per correct answer, 5 for a wrong one, capped at 100. Spent by skills |

Damage settles **when the round closes**, never the moment an answer arrives —
otherwise whoever pressed first would knock the other out before they had a
chance to answer the same question. A stunned player is refused with the
`STUNNED` error code and is not counted as owing an answer, so the round still
closes as soon as their opponent replies.

Dropping someone to 0 HP ends the match immediately with
`end_reason = KNOCKOUT`; the round that landed the blow still reports its result
first. Ties break on **health left**, then score, then correct answers, then
total answer time; still level is a draw. Two players who knock each other out
in the same round are level on health and fall through to the point tiebreaks.

### Classes and skills

A player picks one of three classes, which is the stat block they bring into a
match. A class only ever changes the numbers fed to `combat.resolve_blow`; it
never adds a branch to how a blow resolves, so adding one is a data change.

| Class | Health | Damage | Starting mana |
|---|---|---|---|
| Chiến binh (warrior) | 130 | x0.9 | 10 |
| Pháp sư (mage) | 80 | x1.25 | 30 |
| Sát thủ (assassin) | 100 | x1.05 | 20 |

The first class is free; changing later costs 500 gold and clears the equipped
bar. Unlocked skills are never taken away, so switching back finds the tree
intact.

Skills live in `skills` as a tree: `parent_code` is the edge, and a node opens
once its parent is owned, the level is high enough and the gold is there. Two
neutral starters are granted with the first profile, so nobody ever enters a
match with an empty bar. Three skills can be equipped at a time, and **only an
equipped skill can be cast**, which makes a build a decision taken before the
fight rather than during it.

**Ultimates are bought with study, not gold.** They carry `unlock_kind =
UNIT_COMPLETION` and open when every lesson of the unit they are bound to is
complete. That completion is derived on demand from `user_lesson_progress`
(`UserProgressRepository.completed_unit_ids`) rather than stored, so it cannot
fall out of step; bank lessons are excluded, exactly as the course tree
excludes them.

Casting is `skill.use`; the server answers with `skill.used` to **both** players
so each can see what the other did, with a `private` block filled in only for
the caster (the hidden options of a reveal, for instance). A skill needs an open
round, enough mana, and may be cast once per round; a stunned player cannot cast
at all. `magnitude` is read against `effect` — percent, thousandths, health,
seconds — which is why the catalog is data and the meaning is code.

Time penalties do not shorten the round. The round is one `asyncio.wait_for` on
one clock, so a penalty becomes a shorter deadline *for one player*, enforced
when their answer arrives. Effects are resolved into the numbers
`resolve_blow` already takes, so no skill adds a code path to the engine.

### Rewards

Results feed an Elo rating (K=32, starting at 1000) and, separately, the game
profile in `user_game_profiles`:

| Outcome | Experience | Gold |
|---|---|---|
| Win | +50 +5 per correct answer (+25 for a knockout) | +25 +2 per correct answer |
| Win because the opponent quit | +40 | +20 |
| Draw | +25 | +12 |
| Loss (played to the end) | +15 | +8 |
| Forfeit / timeout | **-30**, floored at the current level | 0 |

Only quitting costs experience, and the floor means a penalty can never take a
level away — levels gate skills that have already been unlocked. Payouts are
idempotent: every one writes a row to `gold_transactions` keyed by
`(user_id, reason, match_id)`, and settling the same match twice cannot insert a
second row, so it cannot pay twice. A chest uses the same trick on
`loot_grants`, keyed by `(user_id, match_id)`.

### Energy, streaks and chests

A match costs one energy out of five. Energy regenerates **lazily** — nothing
runs in the background, the stored value is read against how long it has been
sitting — one point every 30 minutes, and `energy_updated_at` advances only by
whole intervals so the leftover fraction is carried rather than reset by each
read.

The fast way back to a full bar is to study: finishing a lesson for the first
time refills two. That is the intended loop — the way out of an empty bar is a
lesson, not a wait.

Energy is checked when a player queues, but only **taken** in
`persistence.start_players`, after the question draw has succeeded and before
the match flips to IN_PROGRESS. Everything that can go wrong before that point
(no questions, a cancelled lobby, a dropped queue entry) therefore costs
nothing and needs no refund. Two windows need compensating. A failure writing the match row, where
`persisted` is still false, is covered by `_abort` calling `refund_start`. A
process that dies mid-match is covered at the next startup:
`abandon_orphaned_matches` returns the stranded players and hands their energy
back. Refunding from a cancelled task is deliberately not attempted — awaiting
the database while being cancelled is exactly the fragile thing to avoid.

A daily streak counts calendar days **in the learner's timezone (UTC+7)**, not
UTC: a UTC boundary would roll the day over at 07:00 local and break a streak
mid-session. Both finishing a lesson and playing a match count. The streak buys
a small head start in the next match — up to +15 health and +10 opening mana —
capped deliberately low, so it rewards the habit without making a long-running
account unbeatable.

Every finished match rolls a chest. Equipment moves the same three numbers a
class moves and nothing else, and the whole three-slot set is capped at +20
health, +150 damage permille and +15 mana. Skins and cards carry no bonus at
all. Rolls take an injected `Random`, so the distribution is testable with a
fixed seed.

### Seasons

`duo_ratings` is the all-time rating and is never reset. `season_ratings` is a
separate projection that the ladder resets: Đồng → Bạc → Vàng → Bạch kim → Kim
cương → Cao thủ, derived from the rating rather than stored so the two can never
disagree. A new season opens a player at `soft_reset(previous)` — 70% carried
over — computed the first time they play in it, which keeps a rollover O(1)
instead of rewriting every row.

Rollover rides on the housekeeping sweep that already runs every minute, so
seasons need no scheduler of their own. `GET /duo/leaderboard` serves the
current season by default; `?season=all_time` serves the never-reset board.

Matches run over a single WebSocket. Because a handshake cannot carry an
`Authorization` header, the access token goes in the query string:

```
ws://host/api/v1/duo/ws?token=<access-token>
```

Every frame in both directions is `{"type": "<name>", "data": {...}}`.

**Client → server:** `queue.join`, `queue.leave` (random matchmaking),
`room.create`, `room.join` (play a friend by 6-character room code),
`match.start` (host only), `answer.submit`, `skill.use`, `match.leave`,
`chat.send`, `ping`.

**Server → client:** `connected`, `queue.waiting`, `queue.left`, `queue.timeout`,
`room.created`, `match.found`, `match.started`, `round.start`,
`round.opponent_answered`, `round.result`, `match.resume`,
`opponent.disconnected`, `opponent.reconnected`, `skill.used`,
`match.finished`, `chat.message`, `pong`, `error`.

Wherever a frame names a player — `connected`, `match.found`, `match.resume`,
and the `opponent` on a history row or room preview — it carries the same
**player card**: `id`, `username`, `avatar_url`, `rating`, `tier`, `level`,
`class_code`, `day_streak` (`app/schemas/game/player_card.py`). Identity comes
from `users`, standing from `user_game_profiles` and the ladder; `tier` and
`level` are derived, never stored, so neither can disagree with the rating or
the experience behind it. A player with no game profile yet still gets a card,
at level 1 with no class.

`round.start` never contains the answer — it is revealed in `round.result`.
`round.start`, `round.result` and `match.resume` are built per player rather
than broadcast, because health, mana, combo and the stun flag differ between
the two sides. `match.finished` carries `rating`, `exp`, `gold`, `loot`, `season`, `streak`,
`your_hp_left` and `opponent_hp_left`.

Errors carry a stable `code` (`ROOM_NOT_FOUND`, `ROOM_FULL`, `ALREADY_IN_MATCH`,
`NOT_HOST`, `ROUND_CLOSED`, `ALREADY_ANSWERED`, `INVALID_OPTION`, `STUNNED`,
`SKILL_NOT_EQUIPPED`, `NOT_ENOUGH_MANA`, `SKILL_ALREADY_USED_THIS_ROUND`,
`ROUND_NOT_OPEN`, `NOT_ENOUGH_ENERGY`, `INVALID_PAYLOAD`, …) so clients can
branch on it.

Dropping out does not end the match: the player has 30 seconds to reconnect
(open a new socket with the same token — `connected` reports `active_match_id`,
followed by `match.resume` carrying the current score, round, health, mana and
combo). Past that they forfeit.

Match state is held in the process, so **run a single worker**. Scaling out
requires replacing `app/services/duo/registry.py` with a shared store; nothing
above it needs to change.

To exercise the whole feature against a running server:

```bash
uvicorn app.main:app --port 8000
python -m scripts.duo_smoke            # in another shell
```

For Google sign-in, the Android app sends `{"id_token": "<google-id-token>"}` over
HTTPS. Never send an unverified Google user ID as proof of identity. Existing local
accounts are not linked automatically; account linking must be performed by an already
authenticated user.

## PvE — fighting a lesson

Studying alone used to pay nothing: `POST /challenges/{id}/check` recorded the
answer and refilled energy, but experience and gold only ever came out of duo.
Since level and gold are what unlock skills, a player who only studied stayed at
level 1 forever. A lesson battle closes that hole without inventing a second
economy: **every gate on the learn path is guarded by a monster, and the
questions of that lesson are the fight**.

Answer correctly and you strike; answer wrongly, or let the clock run out, and
the monster strikes back. A perfect run takes no damage at all.

### How it joins the learn path

Each answer inside a battle goes through the very same
`ProgressService.check_answer` the ordinary lesson screen calls, in its own
short session. That call is the **only** judge of right and wrong — the engine
keeps an answer key, but only to reveal a question nobody answered and to hide
wrong options for a `REMOVE_OPTIONS` skill.

The consequence is that a battle needs no code at the progress layer at all: the
attempt is recorded, the lesson rolls forward to `COMPLETED`, energy refills,
the streak extends and unit-completion skills unlock exactly as if the player
had used the study screen. `battle.finished` reports where the lesson stands, so
the client can see both layers moved together.

`POST /challenges/{id}/check` is untouched and still works. PvE runs **beside**
the study screen, not instead of it.

### Which monster guards which lesson

Derived, never stored — so 945 gates need no rows of their own and an imported
batch of lessons is guarded the moment it lands:

```
tier    = 1 + min(5, unit.order_index // 8)      # six tiers over the path
is_boss = the lesson closing its unit (bank lessons are not on the path)
```

The catalog is twelve archetypes, six tiers of an ordinary monster and its boss,
upserted by `code` on every start like the class and item catalogs. A monster is
never deleted; retiring one is `is_active = false`.

| Tier | Ordinary | Boss |
| --- | --- | --- |
| 1 | Slime (60 hp, 8 dmg) | Slime Vương (110 hp, 12) |
| 2 | Yêu tinh (90, 12) | Tù trưởng Yêu tinh (140, 15) |
| 3 | Sói hoang (110, 14) | Sói đầu đàn (160, 17) |
| 4 | Golem đá (130, 16) | Thần đá (180, 19) |
| 5 | Oán linh (150, 18) | Vua Lich (200, 21) |
| 6 | Rồng con (170, 20) | Rồng lửa (220, 22) |

### The fight

The player's side is duo's combat maths unchanged (`app/services/game/combat.py`
— speed, quick and heavy strikes, combos, class and equipment multipliers), so a
build that works in PvP works here. Health, mana and the three equipped skills
come from the same `LoadoutBuilder`.

Only the monster's turn is new, and it is deliberately the simplest thing that
can be balanced:

| | |
| --- | --- |
| Monster's turn | A flat `attack_damage`, and only when the answer was wrong or missing |
| Enrage | After `enrage_after_rounds` rounds its damage is multiplied, so a long lesson cannot be farmed as a safe place to sit |
| Intent | `round.start` says what a miss will cost, shield included — without it a round reads as a coin toss |
| Weakness | `weak_topic_id` feeds `resolve_blow`'s `element_multiplier` (x1.5). Wired, but seeded NULL: no balance leans on it yet |
| `TIME_PENALTY`, `MANA_BURN` | Refused with `SKILL_NO_TARGET` **before** the mana is spent — a monster keeps no clock and no mana |

A battle ends when the monster drops (`WON` / `MONSTER_DOWN`), when the player
drops (`LOST` / `PLAYER_DOWN`), when the questions run out with the monster
still up (`LOST` / `OUT_OF_QUESTIONS`), or when the player walks out or drops
their connection (`ABANDONED` / `LEFT`).

**Losing costs nothing.** No energy is charged, nothing is taken, and the
answers already given keep their progress — so unlike duo there is no 30-second
grace window and no resume: there would be nothing left to rescue.

### Rewards

| | Experience | Gold |
| --- | --- | --- |
| Win | +20 +3 per correct answer | +10 +1 per correct answer |
| Boss | +30 | +15 |
| No damage taken | +15 | — |
| Beating a lesson again | 30% of the above, rounded down | 30%, rounded down |
| Loss or walkout | 0 | 0 |

Deliberately smaller than duo (a duo win is +50 +5 per answer) so PvP stays the
fastest climb and PvE the reliable one. Chests drop from bosses only.

Anti-grinding reuses the ledger that already exists rather than adding a
"cleared" column: the first clear is a `BATTLE_WIN` row keyed on the **lesson**,
so a lesson pays full price exactly once and forever; every run after it is a
`BATTLE_REPLAY` row keyed on the **battle**. `first_clear` in `battle.finished`
is simply whether that first row was accepted. PvE never touches Elo or the
season ladder.

### Protocol

```
ws://host/api/v1/battles/ws?token=<access-token>
```

Frames are `{"type": "<name>", "data": {...}}`, as in duo, but the events and
error codes are PvE's own: a few names read the same, and that is the price of
the two features never being able to break each other.

**Client → server:** `battle.start` (`lesson_id`, optional
`time_per_question`), `answer.submit`, `skill.use`, `battle.leave`, `ping`.

**Server → client:** `connected`, `battle.started`, `round.start`,
`round.result`, `skill.used`, `battle.finished`, `pong`, `error`.

`round.start` never contains the answer — it carries the question, the clock,
both health bars and `monster_intent`. `round.result` carries the correct
options, the explanation, the blow that landed or the blow taken, and the new
totals. `battle.finished` carries `outcome`, `end_reason`, `first_clear`, `exp`,
`gold`, `loot`, `streak` and `lesson_progress`.

Errors carry a stable `code` (`BATTLE_ALREADY_ACTIVE`, `LESSON_NOT_FOUND`,
`NO_QUESTIONS_AVAILABLE`, `MONSTER_UNAVAILABLE`, `NOT_IN_BATTLE`,
`ROUND_CLOSED`, `ALREADY_ANSWERED`, `INVALID_OPTION`, `SKILL_NOT_EQUIPPED`,
`NOT_ENOUGH_MANA`, `SKILL_ALREADY_USED_THIS_ROUND`, `SKILL_NO_TARGET`,
`INVALID_PAYLOAD`, `UNKNOWN_EVENT`).

Battle state is held in the process, exactly like duo, so the **single worker**
constraint now covers both features. Battles left running by a stopped process
are closed at start-up; nothing is refunded because nothing was charged.

To exercise the whole feature against a running server:

```bash
uvicorn app.main:app --port 8000
python -m scripts.pve_smoke            # in another shell
```

## Quality checks

```bash
pytest
ruff check .
mypy app
```

Create a new migration after changing a model:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

Rate limiting should be enforced at the reverse proxy/API gateway and, for distributed
deployments, backed by a shared store such as Redis.

Only enable Uvicorn proxy headers when the API is behind a known reverse proxy, and set
`--forwarded-allow-ips` to that proxy's address instead of trusting every source.
