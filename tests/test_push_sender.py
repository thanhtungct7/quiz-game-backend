from pathlib import Path

import pytest
from firebase_admin import exceptions, messaging

from app.core.config import Settings
from app.services.notification.messages import season_started
from app.services.notification.push_sender import (
    MAX_TOKENS_PER_REQUEST,
    DisabledPushSender,
    FirebasePushSender,
    build_push_sender,
    is_dead_token_error,
    to_multicast,
)


def test_uninstalled_and_foreign_tokens_are_dead() -> None:
    assert is_dead_token_error(messaging.UnregisteredError("gone"))
    assert is_dead_token_error(messaging.SenderIdMismatchError("another project"))


def test_transient_and_payload_errors_do_not_kill_a_token() -> None:
    assert not is_dead_token_error(exceptions.UnavailableError("try later"))
    assert not is_dead_token_error(exceptions.InvalidArgumentError("bad payload"))


async def test_a_large_audience_is_split_into_fcm_sized_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[list[str]] = []

    async def fake_send(
        multicast: messaging.MulticastMessage, app: object = None
    ) -> messaging.BatchResponse:
        requests.append(list(multicast.tokens))
        return messaging.BatchResponse(
            [
                messaging.SendResponse(None, messaging.UnregisteredError("gone"))
                if token == "dead"  # noqa: S105
                else messaging.SendResponse({"name": "sent"}, None)
                for token in multicast.tokens
            ]
        )

    monkeypatch.setattr(messaging, "send_each_for_multicast_async", fake_send)
    sender = FirebasePushSender("unused.json")
    monkeypatch.setattr(sender, "firebase_app", lambda: None)
    tokens = [f"t{i}" for i in range(MAX_TOKENS_PER_REQUEST)] + ["dead"]

    dead = await sender.send(tokens, season_started("Mùa 10/2026"))

    assert [len(request) for request in requests] == [MAX_TOKENS_PER_REQUEST, 1]
    assert dead == ["dead"]


def test_the_push_names_its_android_channel_and_route() -> None:
    multicast = to_multicast(["t"], season_started("Mùa 10/2026"))

    assert multicast.android.notification.channel_id == "season"
    assert multicast.data == {"route": "leaderboard"}


def test_without_credentials_pushes_are_only_logged() -> None:
    config = Settings(google_web_client_id="client", firebase_credentials_file="")

    assert isinstance(build_push_sender(config), DisabledPushSender)


def test_with_credentials_pushes_go_through_firebase(tmp_path: Path) -> None:
    key = tmp_path / "firebase.json"
    key.write_text("{}")
    config = Settings(google_web_client_id="client", firebase_credentials_file=str(key))

    assert isinstance(build_push_sender(config), FirebasePushSender)


def test_a_credentials_file_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="FIREBASE_CREDENTIALS_FILE"):
        Settings(
            google_web_client_id="client",
            firebase_credentials_file=str(tmp_path / "missing.json"),
        )
