import email
import imaplib
import smtplib
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from config import config
from tools.base import BaseTool, ToolResult


class MailTool(BaseTool):
    name = "mail"
    description = (
        "E-Mail-Postfach: neueste oder ungelesene E-Mails lesen (IMAP) und E-Mails versenden (SMTP). "
        "Zum Senden müssen Empfänger, Betreff und Text vollständig angegeben werden."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "unread", "send"],
                "description": "read = neueste Mails, unread = nur ungelesene, send = E-Mail versenden",
            },
            "count": {"type": "integer", "description": "Anzahl Mails beim Lesen (Standard 5, max 20)."},
            "to": {"type": "string", "description": "Empfängeradresse (send)."},
            "subject": {"type": "string", "description": "Betreff (send)."},
            "body": {"type": "string", "description": "Nachrichtentext (send)."},
        },
        "required": ["action"],
    }

    def _check_config(self) -> bool:
        return bool(config.EMAIL_ACCOUNT and config.EMAIL_PASSWORD)

    def confirmation_prompt(self, **kwargs) -> Optional[str]:
        if kwargs.get("action") == "send":
            return (
                f"E-Mail senden an {kwargs.get('to', '?')}\n"
                f"Betreff: {kwargs.get('subject', '(kein Betreff)')}\n"
                f"Text: {(kwargs.get('body') or '')[:300]}"
            )
        return None

    # ── Lesen ────────────────────────────────────────────────────────────────

    @staticmethod
    def _decode(value: str) -> str:
        out = ""
        for part, enc in decode_header(value or ""):
            out += part.decode(enc or "utf-8", errors="ignore") if isinstance(part, bytes) else str(part)
        return out.strip()

    def _extract_body(self, msg) -> str:
        try:
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                        payload = part.get_payload(decode=True)
                        if payload:
                            return payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    return payload.decode(msg.get_content_charset() or "utf-8", errors="ignore")
        except Exception:
            pass
        return ""

    def fetch_emails(self, max_count: int = 5, unread_only: bool = False) -> ToolResult:
        if not self._check_config():
            return ToolResult.fail("E-Mail ist nicht konfiguriert (EMAIL_ACCOUNT / EMAIL_PASSWORD in .env fehlen).")
        try:
            server = config.IMAP_SERVER or "imap." + config.EMAIL_ACCOUNT.split("@")[-1]
            mail = imaplib.IMAP4_SSL(server, config.IMAP_PORT)
            mail.login(config.EMAIL_ACCOUNT, config.EMAIL_PASSWORD)
            mail.select("inbox")

            status, messages = mail.search(None, "UNSEEN" if unread_only else "ALL")
            label = "ungelesene" if unread_only else "neueste"
            if status != "OK" or not messages[0].strip():
                mail.logout()
                return ToolResult.ok(f"Keine {label} E-Mails im Postfach.", data=[])

            ids = messages[0].split()[-max_count:]
            results = []
            for e_id in reversed(ids):
                _, msg_data = mail.fetch(e_id.decode() if isinstance(e_id, bytes) else str(e_id), "(BODY.PEEK[])")
                for part in msg_data:
                    if not isinstance(part, tuple):
                        continue
                    msg = email.message_from_bytes(part[1])
                    body = self._extract_body(msg).strip()[:300].replace("\n", " ")
                    results.append(
                        {
                            "subject": self._decode(msg.get("Subject", "")) or "Kein Betreff",
                            "from": self._decode(msg.get("From", "Unbekannt")),
                            "date": msg.get("Date", ""),
                            "body": body,
                        }
                    )
            mail.logout()

            formatted = "\n\n".join(
                f"#{i} Von: {m['from']}\n   Betreff: {m['subject']}\n   Datum: {m['date']}\n   Vorschau: {m['body'] or '(kein Text)'}"
                for i, m in enumerate(results, 1)
            )
            return ToolResult.ok(f"{len(results)} {label} E-Mails:\n{formatted}", data=results)
        except Exception as e:
            return ToolResult.fail(f"Fehler beim Abrufen der E-Mails: {e}")

    # ── Senden ───────────────────────────────────────────────────────────────

    def send_email(self, recipient: str, subject: str, body: str) -> ToolResult:
        if not self._check_config():
            return ToolResult.fail("E-Mail ist nicht konfiguriert (EMAIL_ACCOUNT / EMAIL_PASSWORD in .env fehlen).")
        try:
            host = config.SMTP_SERVER or "smtp." + config.EMAIL_ACCOUNT.split("@")[-1]
            msg = MIMEMultipart()
            msg["From"] = config.EMAIL_ACCOUNT
            msg["To"] = recipient
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(host, config.SMTP_PORT, timeout=20) as server:
                server.starttls()
                server.login(config.EMAIL_ACCOUNT, config.EMAIL_PASSWORD)
                server.sendmail(config.EMAIL_ACCOUNT, recipient, msg.as_string())
            return ToolResult.ok(f"E-Mail an {recipient} mit Betreff '{subject}' gesendet.")
        except Exception as e:
            return ToolResult.fail(f"Fehler beim Senden der E-Mail: {e}")

    def execute(self, action: str = "read", **kwargs) -> ToolResult:
        if action in ("read", "unread"):
            count = max(1, min(int(kwargs.get("count") or 5), 20))
            return self.fetch_emails(max_count=count, unread_only=(action == "unread"))
        if action == "send":
            to = (kwargs.get("to") or "").strip()
            subject = (kwargs.get("subject") or "").strip()
            body = kwargs.get("body") or ""
            if not to or "@" not in to:
                return ToolResult.fail("Bitte eine gültige Empfängeradresse angeben ('to').")
            if not subject:
                return ToolResult.fail("Bitte einen Betreff angeben ('subject').")
            if not body.strip():
                return ToolResult.fail("Bitte einen Nachrichtentext angeben ('body').")
            return self.send_email(to, subject, body)
        return ToolResult.fail(f"Unbekannte Mail-Aktion: {action}")
