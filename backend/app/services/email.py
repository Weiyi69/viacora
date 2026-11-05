import logging
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutgoingEmail:
    recipient: str
    subject: str
    body: str


class EmailSender(Protocol):
    def send(self, message: OutgoingEmail) -> None: ...


class LocalOutboxSender:
    def __init__(self, path: Path) -> None:
        self._path = path

    def send(self, message: OutgoingEmail) -> None:
        email = EmailMessage()
        email["To"] = message.recipient
        email["From"] = "Viacora <no-reply@localhost>"
        email["Subject"] = message.subject
        email["Date"] = datetime.now(timezone.utc)
        email.set_content(message.body)
        self._path.mkdir(parents=True, exist_ok=True)
        filename = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}.eml"
        destination = self._path / filename
        temporary = destination.with_suffix(".tmp")
        temporary.write_bytes(email.as_bytes())
        temporary.replace(destination)


class SmtpEmailSender:
    def __init__(
        self, host: str, port: int, username: str, password: str, from_address: str
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_address = from_address

    def send(self, message: OutgoingEmail) -> None:
        email = EmailMessage()
        email["To"] = message.recipient
        email["From"] = self._from_address
        email["Subject"] = message.subject
        email["Date"] = datetime.now(timezone.utc)
        email.set_content(message.body)
        context = ssl.create_default_context()
        with smtplib.SMTP(self._host, self._port, timeout=15) as client:
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
            client.login(self._username, self._password)
            client.send_message(email)


def get_email_sender() -> EmailSender:
    settings = get_settings()
    if settings.smtp_host:
        if not all(
            (
                settings.smtp_username,
                settings.smtp_password,
                settings.smtp_from_address,
            )
        ):
            raise RuntimeError("SMTP settings are incomplete")
        return SmtpEmailSender(
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_username,
            settings.smtp_password,
            settings.smtp_from_address,
        )
    return LocalOutboxSender(settings.email_outbox_path)


def deliver_email(message: OutgoingEmail) -> bool:
    try:
        get_email_sender().send(message)
    except (OSError, smtplib.SMTPException, RuntimeError):
        logger.warning("Authentication email delivery failed")
        return False
    return True
