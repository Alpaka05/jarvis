import imaplib
import smtplib
import email
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Any
from config import config
from tools.base import BaseTool, ToolResult

class MailTool(BaseTool):
    name = "mail"
    description = "Liest E-Mails via IMAP und versendet E-Mails via SMTP."

    def _check_config(self) -> bool:
        return bool(config.EMAIL_ACCOUNT and config.EMAIL_PASSWORD)

    def _extract_body(self, msg) -> str:
        try:
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disp = str(part.get("Content-Disposition", ""))
                    if content_type == "text/plain" and "attachment" not in content_disp:
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset() or "utf-8"
                            return payload.decode(charset, errors="ignore")
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="ignore")
        except Exception:
            pass
        return ""

    def fetch_emails(self, max_count: int = 5, unread_only: bool = False) -> ToolResult:
        if not self._check_config():
            return ToolResult(
                success=False,
                output="E-Mail-Zugangsdaten sind noch nicht in der .env-Datei konfiguriert (EMAIL_ACCOUNT & EMAIL_PASSWORD)."
            )
        try:
            server = config.IMAP_SERVER or "imap." + config.EMAIL_ACCOUNT.split("@")[-1]
            mail = imaplib.IMAP4_SSL(server, config.IMAP_PORT)
            mail.login(config.EMAIL_ACCOUNT, config.EMAIL_PASSWORD)
            mail.select("inbox")

            # Search latest emails in inbox
            if unread_only:
                status, messages = mail.search(None, 'UNSEEN')
                criterion_label = "ungelesene"
            else:
                status, messages = mail.search(None, 'ALL')
                criterion_label = "neueste"

            if status != 'OK' or not messages[0] or not messages[0].strip():
                mail.logout()
                return ToolResult(success=True, output="Keine E-Mails im Postfach gefunden.", data=[])

            email_ids = messages[0].split()
            latest_ids = email_ids[-max_count:]
            results = []

            for e_id in reversed(latest_ids):
                e_id_str = e_id.decode("utf-8") if isinstance(e_id, bytes) else str(e_id)
                _, msg_data = mail.fetch(e_id_str, '(BODY.PEEK[])')
                for response_part in msg_data:

                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        subject_header = msg.get("Subject", "Kein Betreff")
                        subject_parts = decode_header(subject_header)
                        subject_text = ""
                        for part, encoding in subject_parts:
                            if isinstance(part, bytes):
                                subject_text += part.decode(encoding or "utf-8", errors="ignore")
                            else:
                                subject_text += str(part)
                        
                        from_str = msg.get("From", "Unbekannt")
                        date_str = msg.get("Date", "")
                        body_text = self._extract_body(msg)
                        short_body = body_text.strip()[:300].replace("\n", " ")
                        
                        results.append({
                            "subject": subject_text.strip() or "Kein Betreff",
                            "from": from_str,
                            "date": date_str,
                            "body": short_body
                        })

            mail.logout()
            formatted_list = []
            for idx, m in enumerate(results, 1):
                formatted_list.append(
                    f"E-Mail #{idx}:\n  Von: {m['from']}\n  Betreff: {m['subject']}\n  Datum: {m['date']}\n  Inhalt: {m['body'] or '(Kein Textinhalt)'}"
                )

            formatted = "\n\n".join(formatted_list)
            return ToolResult(
                success=True,
                output=f"E-Mails ({criterion_label}, {len(results)}):\n{formatted}",
                data=results
            )
        except Exception as e:
            return ToolResult(success=False, output=f"Fehler beim Abrufen der E-Mails: {str(e)}")

    def send_email(self, recipient: str, subject: str, body: str) -> ToolResult:
        if not self._check_config():
            return ToolResult(
                success=False,
                output="E-Mail-Zugangsdaten fehlen in .env (EMAIL_ACCOUNT & EMAIL_PASSWORD)."
            )
        try:
            server_host = config.SMTP_SERVER or "smtp." + config.EMAIL_ACCOUNT.split("@")[-1]
            msg = MIMEMultipart()
            msg["From"] = config.EMAIL_ACCOUNT
            msg["To"] = recipient
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            server = smtplib.SMTP(server_host, config.SMTP_PORT)
            server.starttls()
            server.login(config.EMAIL_ACCOUNT, config.EMAIL_PASSWORD)
            server.sendmail(config.EMAIL_ACCOUNT, recipient, msg.as_string())
            server.quit()

            return ToolResult(
                success=True,
                output=f"E-Mail an '{recipient}' mit Betreff '{subject}' erfolgreich gesendet."
            )
        except Exception as e:
            return ToolResult(success=False, output=f"Fehler beim Senden der E-Mail: {str(e)}")

    def execute(self, action: str = "read", **kwargs) -> ToolResult:
        if action in ("read", "unread", "list", "all"):
            count = kwargs.get("count", 5)
            unread_only = (action == "unread")
            return self.fetch_emails(max_count=count, unread_only=unread_only)
        elif action == "send":
            to = kwargs.get("to") or kwargs.get("recipient")
            subject = kwargs.get("subject", "Kein Betreff")
            body = kwargs.get("body", "")
            if not to:
                return ToolResult(success=False, output="Bitte gib eine Empfänger-Adresse an ('to').")
            return self.send_email(recipient=to, subject=subject, body=body)
        else:
            return ToolResult(success=False, output=f"Unbekannte Mail-Aktion: {action}")
