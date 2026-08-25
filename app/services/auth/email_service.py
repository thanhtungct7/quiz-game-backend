import asyncio
import html
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol
from urllib.parse import quote

from app.core.config import Settings


class EmailService(Protocol):
    async def send_password_reset(
            self,
            recipient: str,
            reset_token: str,
    ) -> None:
        """Send an email to the recipient with the provided reset token."""


class SmtpEmailService:
    def __init__(
            self,
            config: Settings
    ) -> None:
        self.config = config

    async def send_password_reset(
            self, 
            recipient: str,
            reset_token: str,
    ) -> None:
        encoded_token = quote(reset_token, safe="")
        reset_url = str(self.config.password_reset_url)
        separator = "&" if "?" in reset_url else "?"
        reset_link = f"{reset_url}{separator}token={encoded_token}"

        message = EmailMessage()
        message["Subject"] = "Đặt lại mật khẩu Quiz Game của bạn"
        message["From"] = str(self.config.smtp_from_email)
        message["To"] = recipient

        message.set_content("Bạn vừa yêu cầu đặt lại mật khẩu Quiz Game.\n\n"
                            f"Mở liên kết sau để tiếp tục:\n{reset_link}\n\n"
                            "Liên kết này sẽ hết hạn sau 15 phút."
                            "Nếu bạn không yêu cầu đặt lại mật khẩu, hãy bỏ qua email này.\n"
                            )
        safe_link = html.escape(reset_link, quote=True)
        message.add_alternative(
            f"""
<html>
  <body>
    <p>Bạn vừa yêu cầu đặt lại mật khẩu Quiz Game.</p>
    <p>Mở liên kết sau để tiếp tục:</p>
    <p><a href="{safe_link}">{safe_link}</a></p>
    <p>Liên kết này sẽ hết hạn sau 15 phút.</p>
    <p>Nếu bạn không yêu cầu đặt lại mật khẩu, hãy bỏ qua email này.</p>
  </body>
</html>
""", subtype="html",
        )

        # smtplib is blocking; run the connect/login/send on a worker thread so
        # it doesn't stall the event loop for the duration of the SMTP roundtrip.
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()

        if self.config.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                self.config.smtp_host,
                self.config.smtp_port,
                timeout=self.config.smtp_timeout_seconds,
                context=context,
            ) as server:
                self._authenticate_and_send(server, message)
        else:
            with smtplib.SMTP(
                self.config.smtp_host,
                self.config.smtp_port,
                timeout=self.config.smtp_timeout_seconds,
            ) as server:
                if self.config.smtp_start_tls:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                self._authenticate_and_send(server, message)

    def _authenticate_and_send(
            self,
            server: smtplib.SMTP,
            message: EmailMessage,
    ) -> None:
        if self.config.smtp_username is not None:
            password = self.config.smtp_password
            if password is None:
                raise ValueError("SMTP password is missing")

            server.login(self.config.smtp_username,
                         password.get_secret_value(),
                         )

        server.send_message(message)