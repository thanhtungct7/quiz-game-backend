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
| GET | `/api/v1/users/me` | Read the authenticated user |

Use the access token as `Authorization: Bearer <token>`.

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
