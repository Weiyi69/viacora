import smtplib
import ssl
from unittest.mock import MagicMock, Mock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services import email


def test_development_settings_accept_gmail_smtp_configuration() -> None:
    settings = Settings(
        _env_file=None,
        environment="development",
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_username="learner@gmail.com",
        smtp_password="safe-test-app-password",
        smtp_from_address="learner@gmail.com",
        smtp_starttls=True,
    )

    assert settings.smtp_host == "smtp.gmail.com"
    assert settings.smtp_port == 587
    assert settings.smtp_starttls is True


def test_smtp_configuration_requires_all_values() -> None:
    with pytest.raises(ValidationError, match="required together"):
        Settings(
            _env_file=None,
            environment="development",
            smtp_host="smtp.gmail.com",
            smtp_username="learner@gmail.com",
        )


def test_email_sender_uses_gmail_configuration_in_development(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        environment="development",
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_username="learner@gmail.com",
        smtp_password="safe-test-app-password",
        smtp_from_address="learner@gmail.com",
        smtp_starttls=True,
    )
    monkeypatch.setattr(email, "get_settings", lambda: settings)

    sender = email.get_email_sender()

    assert isinstance(sender, email.SmtpEmailSender)
    assert sender._host == "smtp.gmail.com"
    assert sender._port == 587


def test_gmail_smtp_delivery_uses_verified_starttls(monkeypatch) -> None:
    context = ssl.create_default_context()
    smtp_client = Mock()
    smtp_context = MagicMock()
    smtp_context.__enter__.return_value = smtp_client
    smtp_factory = Mock(return_value=smtp_context)
    monkeypatch.setattr(email.smtplib, "SMTP", smtp_factory)
    monkeypatch.setattr(email.ssl, "create_default_context", lambda: context)
    sender = email.SmtpEmailSender(
        "smtp.gmail.com", 587, "learner@gmail.com", "test-app-password", "learner@gmail.com"
    )

    sender.send(email.OutgoingEmail("recipient@example.com", "Test", "Message"))

    smtp_factory.assert_called_once_with("smtp.gmail.com", 587, timeout=15)
    smtp_client.ehlo.assert_called()
    smtp_client.starttls.assert_called_once_with(context=context)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    smtp_client.login.assert_called_once_with("learner@gmail.com", "test-app-password")
    smtp_client.send_message.assert_called_once()


def test_smtp_failure_is_reported_without_leaking_details(monkeypatch) -> None:
    class FailedSender:
        def send(self, message: email.OutgoingEmail) -> None:
            raise smtplib.SMTPAuthenticationError(535, b"private credential detail")

    warning = Mock()
    monkeypatch.setattr(email, "get_email_sender", lambda: FailedSender())
    monkeypatch.setattr(email.logger, "warning", warning)
    message = email.OutgoingEmail(
        "recipient@example.com",
        "Verify",
        "https://frontend.example/verify-email#token=private-token",
    )

    assert email.deliver_email(message) is False
    warning.assert_called_once_with("Authentication email delivery failed")
    assert "private credential detail" not in str(warning.call_args)
    assert "private-token" not in str(warning.call_args)
