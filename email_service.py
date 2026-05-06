import asyncio
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from pathlib import Path

from config import SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ADMIN_EMAIL, PROJECT_NAME


class EmailService:
    """Сервис для отправки email через SMTP"""

    def __init__(self):
        self.smtp_server = SMTP_SERVER
        self.smtp_port = SMTP_PORT
        self.smtp_user = SMTP_USER
        self.smtp_password = SMTP_PASSWORD
        self.sender_email = SMTP_USER or ADMIN_EMAIL
        self.sender_name = PROJECT_NAME

    def _send_sync(
        self,
        subject: str,
        to_email: str,
        text_body: Optional[str] = None,
        html_body: Optional[str] = None
    ) -> None:
        """Синхронная отправка email через SMTP"""
        if not self.smtp_user or not self.smtp_password:
            print("[Email] SMTP credentials not configured, skipping email send")
            return

        if not to_email:
            print("[Email] No recipient email provided")
            return

        msg = MIMEMultipart("alternative")
        msg["From"] = f"{self.sender_name} <{self.sender_email}>"
        msg["To"] = to_email
        msg["Subject"] = subject

        # Добавляем plain text версию
        if text_body:
            msg.attach(MIMEText(text_body, "plain", "utf-8"))

        # Добавляем HTML версию (если есть)
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=10) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.sender_email, to_email, msg.as_string())
            print(f"[Email] Sent to {to_email}: {subject}")
        except Exception as exc:
            print(f"[Email] Failed to send to {to_email}: {exc}")

    async def send_email(
        self,
        subject: str,
        to_email: str,
        text_body: Optional[str] = None,
        html_body: Optional[str] = None,
        template: Optional[str] = None,
        template_vars: Optional[dict] = None
    ) -> None:
        """
        Асинхронная отправка email.

        Args:
            subject: Тема письма
            to_email: Email получателя
            text_body: Текстовая версия письма
            html_body: HTML версия письма
            template: Имя HTML шаблона (без расширения) из templates/emails/
            template_vars: Переменные для подстановки в шаблон
        """
        recipient = to_email or ADMIN_EMAIL
        if not recipient:
            print("[Email] No recipient specified and ADMIN_EMAIL not set")
            return

        # Если указан шаблон, загружаем его
        final_html = html_body
        if template:
            template_path = Path(__file__).parent / "templates" / "emails" / f"{template}.html"
            if template_path.exists():
                with open(template_path, "r", encoding="utf-8") as f:
                    final_html = f.read()

                # Подставляем переменные
                if template_vars:
                    for key, value in template_vars.items():
                        final_html = final_html.replace(f"{{{{{key}}}}}", str(value))
            else:
                print(f"[Email] Template not found: {template_path}")

        # Отправляем в отдельном потоке, чтобы не блокировать event loop
        await asyncio.to_thread(
            self._send_sync,
            subject=subject,
            to_email=recipient,
            text_body=text_body,
            html_body=final_html
        )


# Глобальный экземпляр сервиса
email_service = EmailService()


async def send_email(
    subject: str,
    body: str,
    to_email: str = "",
    html_body: Optional[str] = None,
    template: Optional[str] = None,
    template_vars: Optional[dict] = None
) -> None:
    """
    Удобная функция для отправки email (совместима со старым API).

    Args:
        subject: Тема письма
        body: Текстовая версия письма
        to_email: Email получателя (по умолчанию ADMIN_EMAIL)
        html_body: HTML версия письма
        template: Имя HTML шаблона из templates/emails/
        template_vars: Переменные для шаблона
    """
    await email_service.send_email(
        subject=subject,
        to_email=to_email,
        text_body=body,
        html_body=html_body,
        template=template,
        template_vars=template_vars
    )
