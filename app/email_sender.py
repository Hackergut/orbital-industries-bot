"""Email sender for OrbitalTech institutional outreach.

Sends the DocSend framework document before any interaction.
Tracks delivery, opens, and download status per lead.
"""
import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import Config

logger = logging.getLogger(__name__)


# ── Email Template: DocSend Framework Document ──────────────────────────────
DOC_SEND_TEMPLATE = """\
Subject: OrbitalTech — Next Step: Secure Document for Review

Dear {name},

Thank you for your message and for the overview shared.

To move forward efficiently, our standard onboarding process requires a reviewed framework document before any technical integration, data exchange, or joint roadmap discussion. This ensures both parties are aligned on confidentiality, liability, and operational boundaries from day one.

I have prepared the enclosed secure document for your review via DocSend:

{doc_send_link}

The document covers:
  • Mutual NDA and data handling terms
  • Collaboration scope and exclusion clauses
  • Single point of contact and escalation paths

Once reviewed and downloaded, we will immediately share access to our technical sandbox and schedule the kick-off call with our engineering and compliance leads.

Please let us know if your legal team requires any redlines — we typically turn around revisions within 24 hours.

Best regards,

{sender_name}
{sender_title}
OrbitalTech
{company_url} | {company_phone}
"""


class EmailSender:
    """SMTP-backed email sender with OrbitalTech institutional templates."""

    def __init__(self):
        self.host = Config.SMTP_HOST
        self.port = Config.SMTP_PORT
        self.user = Config.SMTP_USER
        self.password = Config.SMTP_PASS
        self.from_name = Config.SMTP_FROM_NAME
        self.from_email = Config.SMTP_FROM_EMAIL
        self.enabled = bool(self.host and self.user and self.password)

    def _build_msg(self, to_email: str, subject: str, body_plain: str, body_html: str = None) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{self.from_name} <{self.from_email}>"
        msg["To"] = to_email
        msg["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
        msg.attach(MIMEText(body_plain, "plain", "utf-8"))
        if body_html:
            msg.attach(MIMEText(body_html, "html", "utf-8"))
        return msg

    def send_doc_send_email(
        self,
        to_email: str,
        to_name: str = "",
        doc_send_link: str = None,
    ) -> dict:
        """Send the pre-interaction DocSend framework email.

        Returns {"sent": bool, "message_id": str|None, "error": str|None}
        """
        if not self.enabled:
            logger.warning("SMTP not configured; cannot send email to %s", to_email)
            return {"sent": False, "message_id": None, "error": "SMTP not configured"}

        doc_send_link = doc_send_link or Config.DOC_SEND_LINK
        if not doc_send_link:
            return {"sent": False, "message_id": None, "error": "DocSend link not configured"}

        company = Config.COMPANY_DATA
        sender_name = f"{company.get('first_name', '')} {company.get('last_name', '')}".strip()
        sender_title = company.get("job_title", "Head of Partnerships")

        body_plain = DOC_SEND_TEMPLATE.format(
            name=to_name or "there",
            doc_send_link=doc_send_link,
            sender_name=sender_name,
            sender_title=sender_title,
            company_url=company.get("company_url", "https://orbitaltech.pro"),
            company_phone=company.get("phone", ""),
        )

        # Extract subject from template first line
        subject = "OrbitalTech — Next Step: Secure Document for Review"
        if body_plain.startswith("Subject:"):
            lines = body_plain.split("\n", 1)
            subject = lines[0].replace("Subject:", "").strip()
            body_plain = lines[1].lstrip("\n")

        # Build minimal HTML version
        body_html = f"""\
<html><body style="font-family:Georgia,serif;color:#222;line-height:1.6;max-width:600px;margin:24px auto;">
<p>Dear {to_name or "there"},</p>
<p>Thank you for your message and for the overview shared.</p>
<p>To move forward efficiently, our standard onboarding process requires a reviewed framework document before any technical integration, data exchange, or joint roadmap discussion. This ensures both parties are aligned on confidentiality, liability, and operational boundaries from day one.</p>
<p>I have prepared the enclosed secure document for your review via DocSend:</p>
<p style="margin:20px 0;"><a href="{doc_send_link}" style="background:#0a0e27;color:#00ff88;padding:12px 20px;text-decoration:none;border-radius:4px;display:inline-block;font-weight:bold;">Open Secure Document</a></p>
<p>The document covers:</p>
<ul>
<li>Mutual NDA and data handling terms</li>
<li>Collaboration scope and exclusion clauses</li>
<li>Single point of contact and escalation paths</li>
</ul>
<p>Once reviewed and downloaded, we will immediately share access to our technical sandbox and schedule the kick-off call with our engineering and compliance leads.</p>
<p>Please let us know if your legal team requires any redlines — we typically turn around revisions within 24 hours.</p>
<p>Best regards,</p>
<p><strong>{sender_name}</strong><br>{sender_title}<br>OrbitalTech<br><a href="{company.get('company_url', '')}">{company.get('company_url', '')}</a> | {company.get('phone', '')}</p>
</body></html>
"""

        msg = self._build_msg(to_email, subject, body_plain, body_html)

        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                server.starttls()
                server.login(self.user, self.password)
                server.sendmail(self.from_email, [to_email], msg.as_string())
            logger.info("DocSend email sent to %s", to_email)
            return {"sent": True, "message_id": None, "error": None}
        except Exception as e:
            logger.error("Failed to send DocSend email to %s: %s", to_email, e)
            return {"sent": False, "message_id": None, "error": str(e)}


# Singleton
_email_sender = None

def get_email_sender() -> EmailSender:
    global _email_sender
    if _email_sender is None:
        _email_sender = EmailSender()
    return _email_sender
