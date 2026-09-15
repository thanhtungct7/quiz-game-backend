"""Delivering a push through Firebase Cloud Messaging.

The only module that touches the Firebase Admin SDK. Everything above it
depends on `PushSender`, so tests -- and a development machine with no Firebase
project -- use a sender that sends nothing.
"""

import logging
import warnings
from collections.abc import Sequence
from typing import Protocol

import firebase_admin
from firebase_admin import credentials, messaging

from app.core.config import Settings, settings
from app.services.notification.messages import PushMessage

logger = logging.getLogger(__name__)

# FCM's ceiling for one multicast request.
MAX_TOKENS_PER_REQUEST = 500
FIREBASE_APP_NAME = "push"


class PushSender(Protocol):
    async def send(self, tokens: Sequence[str], message: PushMessage) -> list[str]:
        """Send to every token, returning the tokens FCM says are gone for good."""
        ...


class DisabledPushSender:
    """Used when no Firebase credentials are configured: logs, sends nothing."""

    async def send(self, tokens: Sequence[str], message: PushMessage) -> list[str]:
        logger.info(
            "Push disabled; would have sent %r to %d device(s)", message.title, len(tokens)
        )
        return []


class FirebasePushSender:
    def __init__(self, credentials_file: str) -> None:
        self.credentials_file = credentials_file
        self._app: firebase_admin.App | None = None

    def firebase_app(self) -> firebase_admin.App:
        """Initialised on first send, so building a sender costs nothing."""
        if self._app is None:
            self._app = firebase_admin.initialize_app(
                credentials.Certificate(self.credentials_file), name=FIREBASE_APP_NAME
            )
        return self._app

    async def send(self, tokens: Sequence[str], message: PushMessage) -> list[str]:
        dead: list[str] = []
        for start in range(0, len(tokens), MAX_TOKENS_PER_REQUEST):
            batch = list(tokens[start : start + MAX_TOKENS_PER_REQUEST])
            response = await messaging.send_each_for_multicast_async(
                to_multicast(batch, message), app=self.firebase_app()
            )
            for token, result in zip(batch, response.responses, strict=True):
                if result.exception is None:
                    continue
                if is_dead_token_error(result.exception):
                    dead.append(token)
                else:
                    logger.warning("Push to one device failed: %s", result.exception)
        return dead


def to_multicast(tokens: list[str], message: PushMessage) -> messaging.MulticastMessage:
    # The SDK flags `tokens` as deprecated in favour of Firebase Installation IDs, yet
    # still sends to them -- and silences the same warning inside its own send path.
    # The app registers FCM registration tokens, so moving off `tokens` means changing
    # what the app registers, not just this call.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return messaging.MulticastMessage(
            tokens=tokens,
            notification=messaging.Notification(title=message.title, body=message.body),
            data=message.data,
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(channel_id=message.channel_id),
            ),
        )


def is_dead_token_error(error: Exception) -> bool:
    """Whether FCM refused a token for good rather than failing this once.

    Unregistered: the app was uninstalled or the token rotated. Sender id
    mismatch: the token belongs to another Firebase project. INVALID_ARGUMENT is
    deliberately not on the list -- it is also what a bad payload gets, and
    reading it as a dead token would switch off every device at once.
    """
    return isinstance(error, messaging.UnregisteredError | messaging.SenderIdMismatchError)


def build_push_sender(config: Settings) -> PushSender:
    if config.firebase_credentials_file is None:
        return DisabledPushSender()
    return FirebasePushSender(config.firebase_credentials_file)


_sender: PushSender | None = None


def get_push_sender() -> PushSender:
    """One per process: the Firebase app it initialises can only be created once."""
    global _sender
    if _sender is None:
        _sender = build_push_sender(settings)
    return _sender
