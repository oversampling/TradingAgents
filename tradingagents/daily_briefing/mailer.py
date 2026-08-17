"""Gmail SMTP delivery without leaking credentials in errors."""

import smtplib
from email.message import EmailMessage


class MailDeliveryError(RuntimeError):
    pass


def send_html_report(sender: str, recipient: str, app_password: str, subject: str, html: str) -> None:
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, recipient, subject
    message.set_content("This report requires an HTML-capable email client.")
    message.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
            smtp.login(sender, app_password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailDeliveryError(f"Email delivery failed: {type(exc).__name__}: {exc}") from exc
