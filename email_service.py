import asyncio
import os
from typing import Optional
from pathlib import Path

import resend

from config import ADMIN_EMAIL, PROJECT_NAME


class EmailService:
    """Сервис для отправки email через Resend"""

    def __init__(self):
        self.sender_name = PROJECT_NAME

    def _send_sync(self, subject, to_email, text_body=None, html_body=None):
        """Синхронная отправка через Resend API"""
        api_key = os.environ.get("re_BzDBehYN_AgEKoFQe87ZZHDPjqgkD1jbS")
        if not api_key:
            print("[Email] RESEND_API_KEY not set, skipping")
            return

        resend.api_key = api_key
        try:
            resend.Emails.send({
                "from": f"{self.sender_name} <onboarding@resend.dev>",
                "to": [to_email],
                "subject": subject,
                "text": text_body or "",
                "html": html_body or "",
            })
            print(f"[Email] Sent to {to_email}: {subject}")
        except Exception as exc:
            print(f"[Email] Failed: {exc}")

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
