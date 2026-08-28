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
| GET | `/api/v1/duo/matches` | Your 1v1 match history |
| GET | `/api/v1/duo/matches/{id}` | One match with its round-by-round detail |
| GET | `/api/v1/duo/me/stats` | Your rating, W/L/D, win rate and streak |
| GET | `/api/v1/duo/leaderboard` | Top players by rating, plus your rank |
| GET | `/api/v1/duo/rooms/{room_code}` | Preview a friend's room before joining |
| WS | `/api/v1/duo/ws` | Play a 1v1 match |

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
`Course → Unit → Lesson → Challenge → ChallengeOption` tree. Each source file
becomes one *group*, laid out as a short stretch of walkable path — by default
3 units × 20 lessons × 10 questions — with everything past that budget parked in
a single `is_bank = true` lesson.

Bank lessons are hidden from `/courses/{id}/tree`, `/units/{id}/lessons` and
`/progress/*`, but their challenges still feed duo matches and `POST
/lessons/{id}/quiz`, which sample the whole `challenges` table. That split is
what keeps a lesson finishable: the bank holds ~156k questions, a lesson holds
ten.

```bash
python -m scripts.import_quiz_bank --reset \
  [--challenges-per-lesson 10] [--lessons-per-unit 20] [--units-per-group 3]
```

`GET /courses/{id}/tree` returns course, units and path lessons in one response
(~100 KB for 48 units / 945 lessons) with an `ETag` computed from the payload.
Clients should cache it and send `If-None-Match`; an unchanged course answers
`304` with no body. It is the only content endpoint that opts out of the global
`Cache-Control: no-store`.

## 1v1 PvP (duo)

Two players answer the same question set at the same time. A correct answer is
worth 500 points plus a speed bonus of up to 500 more; a wrong or missing answer
scores nothing. **Answer time is measured on the server**, so a client cannot
claim to have answered instantly. Ties break on score, then correct answers,
then total answer time; still level is a draw. Results feed an Elo rating
(K=32, starting at 1000).

Matches run over a single WebSocket. Because a handshake cannot carry an
`Authorization` header, the access token goes in the query string:

```
ws://host/api/v1/duo/ws?token=<access-token>
```

Every frame in both directions is `{"type": "<name>", "data": {...}}`.

**Client → server:** `queue.join`, `queue.leave` (random matchmaking),
`room.create`, `room.join` (play a friend by 6-character room code),
`match.start` (host only), `answer.submit`, `match.leave`, `chat.send`, `ping`.

**Server → client:** `connected`, `queue.waiting`, `queue.left`, `queue.timeout`,
`room.created`, `match.found`, `match.started`, `round.start`,
`round.opponent_answered`, `round.result`, `match.resume`,
`opponent.disconnected`, `opponent.reconnected`, `match.finished`,
`chat.message`, `pong`, `error`.

`round.start` never contains the answer — it is revealed in `round.result`.
Errors carry a stable `code` (`ROOM_NOT_FOUND`, `ROOM_FULL`, `ALREADY_IN_MATCH`,
`NOT_HOST`, `ROUND_CLOSED`, `ALREADY_ANSWERED`, `INVALID_OPTION`,
`INVALID_PAYLOAD`, …) so clients can branch on it.

Dropping out does not end the match: the player has 30 seconds to reconnect
(open a new socket with the same token — `connected` reports `active_match_id`,
followed by `match.resume` carrying the current score and round). Past that they
forfeit.

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
