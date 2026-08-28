from email.message import EmailMessage

import pytest

from app.core.config import Settings
from app.services.auth.email_service import SmtpEmailService


class CapturingSmtpEmailService(SmtpEmailService):
    """Builds the message the way production does, but keeps it instead of
    handing it to smtplib."""

    def __init__(self, config: Settings) -> None:
        super().__init__(config)
        self.messages: list[EmailMessage] = []

    def _send_sync(self, message: EmailMessage) -> None:
        self.messages.append(message)


def make_service(reset_url: str) -> CapturingSmtpEmailService:
    return CapturingSmtpEmailService(
        Settings(
            google_web_client_id="test-client-id",
            password_reset_url=reset_url,  # type: ignore[arg-type]
        )
    )


@pytest.mark.asyncio
async def test_the_reset_link_matches_the_scheme_the_android_app_registers() -> None:
    """The manifest filters on scheme `quizgame` + host `reset-password`; a link
    shaped any other way opens a browser instead of the app."""
    service = make_service("quizgame://reset-password")

    await service.send_password_reset(recipient="player@example.com", reset_token="abc123")  # noqa: S106

    body = service.messages[0].get_body(preferencelist=("plain",))
    assert body is not None
    assert "quizgame://reset-password?token=abc123" in body.get_content()


@pytest.mark.asyncio
async def test_a_reset_url_that_already_has_a_query_keeps_it() -> None:
    service = make_service("https://example.com/reset?lang=vi")

    await service.send_password_reset(recipient="player@example.com", reset_token="abc123")  # noqa: S106

    body = service.messages[0].get_body(preferencelist=("plain",))
    assert body is not None
    assert "https://example.com/reset?lang=vi&token=abc123" in body.get_content()


@pytest.mark.asyncio
async def test_a_token_with_url_unsafe_characters_is_escaped() -> None:
    service = make_service("quizgame://reset-password")

    await service.send_password_reset(recipient="player@example.com", reset_token="a+b/c=")  # noqa: S106

    body = service.messages[0].get_body(preferencelist=("plain",))
    assert body is not None
    assert "token=a%2Bb%2Fc%3D" in body.get_content()
