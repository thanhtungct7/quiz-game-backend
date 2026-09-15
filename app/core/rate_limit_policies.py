"""Every rate limit the API enforces, in one place to tune.

Keyed per IP where there is no account yet (and where guessing is the attack),
per user everywhere else -- a carrier NAT puts a whole classroom behind one
address, and one learner must not spend another's allowance.
"""

from app.core.rate_limit import RateLimit

MINUTE = 60
HOUR = 60 * MINUTE

# --- the safety net under everything ---------------------------------------------
GLOBAL_PER_IP = RateLimit(limit=600, window_seconds=MINUTE)

# --- authentication ----------------------------------------------------------------
LOGIN_PER_IP = RateLimit(limit=10, window_seconds=MINUTE)
LOGIN_PER_EMAIL = RateLimit(limit=5, window_seconds=5 * MINUTE)
REGISTER_PER_IP = RateLimit(limit=5, window_seconds=HOUR)
GOOGLE_LOGIN_PER_IP = RateLimit(limit=10, window_seconds=MINUTE)
REFRESH_PER_IP = RateLimit(limit=30, window_seconds=MINUTE)
# Each one sends an email: per address so nobody's inbox can be flooded, per IP so
# nobody can walk a list of addresses.
FORGOT_PASSWORD_PER_IP = RateLimit(limit=10, window_seconds=HOUR)
FORGOT_PASSWORD_PER_EMAIL = RateLimit(limit=3, window_seconds=HOUR)
RESET_PASSWORD_PER_IP = RateLimit(limit=10, window_seconds=15 * MINUTE)

# --- account -------------------------------------------------------------------------
# Every upload is a round trip to Google Drive.
AVATAR_UPLOAD_PER_USER = RateLimit(limit=10, window_seconds=HOUR)
# Sent on every app start and every token rotation; generous, but not a firehose.
DEVICE_REGISTRATION_PER_USER = RateLimit(limit=30, window_seconds=HOUR)

# --- study and exams -------------------------------------------------------------------
CHECK_ANSWER_PER_USER = RateLimit(limit=60, window_seconds=MINUTE)
EXAM_START_PER_USER = RateLimit(limit=10, window_seconds=HOUR)
# Thirty questions to a paper; twice that leaves room for retries.
EXAM_ANSWER_PER_USER = RateLimit(limit=60, window_seconds=MINUTE)

# --- game writes: gold, class, skills, equipment -------------------------------------------
GAME_WRITE_PER_USER = RateLimit(limit=30, window_seconds=MINUTE)

# --- duo -----------------------------------------------------------------------------------
# Room codes are short; this is what keeps them from being enumerated.
ROOM_PREVIEW_PER_USER = RateLimit(limit=20, window_seconds=MINUTE)

# --- WebSockets ---------------------------------------------------------------------------
WS_CONNECT_PER_USER = RateLimit(limit=20, window_seconds=MINUTE)
WS_MESSAGES_PER_SECOND = 20.0
WS_MESSAGE_BURST = 40
