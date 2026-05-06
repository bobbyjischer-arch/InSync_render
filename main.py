import asyncio
import json
import os
import random
import secrets
import shutil
import uuid
import imghdr
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiofiles
import aiofiles.os
import httpx
import requests

from fastapi import FastAPI, Request, Form, HTTPException, Depends, File, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from config import (
    PROJECT_NAME, AES_KEY, APP_URL, IS_PRODUCTION, ADMIN_EMAIL,
)
from crypto import InSyncCipher
from cryptography.fernet import InvalidToken
from database.engine import get_db, init_db, AsyncSessionLocal
from database.models import User, Event, EventParticipant, Notification, PendingRegistration, PasswordReset, REMINDER_CHOICES, REMINDER_LABELS

# ─── Constants ────────────────────────────────────────────────────────────────

cipher = InSyncCipher(AES_KEY)
# Simple in-memory login rate limiter: max 10 attempts per IP per 15 min
_login_attempts: dict[str, list] = defaultdict(list)
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_WINDOW_SECONDS = 900  # 15 minutes
# Guest participation rate limiter: max 5 confirmations per IP per 24 hours
_guest_participation_attempts: dict[str, list] = defaultdict(list)
MAX_GUEST_PARTICIPATIONS = 5
GUEST_WINDOW_HOURS = 24
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AVATAR_DIR = os.path.join(BASE_DIR, "static", "avatars")
COMPLAINTS_FILE = os.path.join(BASE_DIR, "complaints.enc")
EMOJI_LIST = ["📅", "🎉", "🎭", "🍕", "🎸", "⚽", "👥", "❤️", "💼", "🎲"]

os.makedirs(AVATAR_DIR, exist_ok=True)
scheduler = AsyncIOScheduler()


# ─── Complaints helpers (encrypted JSON file) ─────────────────────────────────

def load_complaints() -> list[dict]:
    if not os.path.exists(COMPLAINTS_FILE):
        return []
    try:
        with open(COMPLAINTS_FILE, "r", encoding="utf-8") as f:
            encrypted = f.read().strip()
        if not encrypted:
            return []
        return json.loads(cipher.decrypt(encrypted))
    except (InvalidToken, ValueError) as exc:
        print(f"[complaints] Decryption failed — wrong key or corrupted file: {exc}", file=__import__('sys').stderr)
        return []
    except Exception as exc:
        print(f"[complaints] Failed to load: {exc}", file=__import__('sys').stderr)
        return []


def save_complaints(complaints: list[dict]) -> None:
    encrypted = cipher.encrypt(json.dumps(complaints, ensure_ascii=False))
    with open(COMPLAINTS_FILE, "w", encoding="utf-8") as f:
        f.write(encrypted)


# ─── Email helper (SMTP via email_service) ───────────────────────────────────

from email_service import send_email


# ─── Scheduled job ────────────────────────────────────────────────────────────

async def check_upcoming_events(_app: FastAPI) -> None:
    """
    Smart per-user reminders. Runs every 15 minutes.
    Each user sets their own advance notice (reminder_minutes_before).
    A reminder fires when:  now  <=  event_time - reminder_minutes_before  <  now + 15 min
    That way every user gets exactly one notification at their preferred lead time.
    """
    now = datetime.now(timezone.utc)
    # Look ahead one scheduler tick (15 min) so no window is missed
    tick_end = now + timedelta(minutes=15)

    async with AsyncSessionLocal() as db:
        # Fetch all unsent reminders for registered participants whose events are upcoming
        rows = await db.execute(
            select(User, EventParticipant, Event)
            .join(EventParticipant, EventParticipant.user_id == User.id)
            .join(Event, Event.id == EventParticipant.event_id)
            .where(
                EventParticipant.user_id.isnot(None),
                EventParticipant.reminder_sent == False,
                Event.planned_datetime > now,          # event hasn't happened yet
            )
        )

        for user, ep, event in rows.all():
            remind_at = event.planned_datetime - timedelta(
                minutes=user.reminder_minutes_before
            )
            # Fire if the reminder window falls inside this tick
            if now <= remind_at < tick_end:
                label = REMINDER_LABELS.get(
                    user.reminder_minutes_before,
                    f"за {user.reminder_minutes_before} минут"
                )
                db.add(
                    Notification(
                        user_id=user.id,
                        message=(
                            f"🔔 Напоминание ({label}): "
                            f"событие «{event.title}» начнётся "
                            f"{format_datetime(event.planned_datetime)}!"
                        ),
                        is_read=False,
                    )
                )
                ep.reminder_sent = True

                # Отправка email напоминания
                if user.email:
                    event_date = event.planned_datetime.strftime("%d.%m.%Y")
                    event_time = event.planned_datetime.strftime("%H:%M")
                    location_html = f'<div class="event-detail"><span class="event-icon">📍</span><span><strong>Место:</strong> {event.location}</span></div>' if event.location else ""
                    description_html = f'<div class="event-detail"><span class="event-icon">📝</span><span><strong>Описание:</strong> {event.description}</span></div>' if event.description else ""

                    await send_email(
                        subject=f"InSync — Напоминание о событии «{event.title}»",
                        body=(
                            f"Привет, {user.first_name}!\n\n"
                            f"Напоминаем о предстоящем событии {label}:\n\n"
                            f"{event.emoji or '📅'} {event.title}\n"
                            f"Дата: {event_date}\n"
                            f"Время: {event_time}\n"
                            + (f"Место: {event.location}\n" if event.location else "")
                            + (f"Описание: {event.description}\n" if event.description else "")
                            + f"\nДо встречи!"
                        ),
                        to_email=user.email,
                        template="event_reminder",
                        template_vars={
                            "name": user.first_name,
                            "reminder_label": label,
                            "event_emoji": event.emoji or "📅",
                            "event_title": event.title,
                            "event_date": event_date,
                            "event_time": event_time,
                            "event_location": location_html,
                            "event_description": description_html,
                            "event_link": f"{APP_URL}/event/{event.id}"
                        }
                    )

        await db.commit()


async def cleanup_login_attempts():
    """Очистка старых записей из _login_attempts для предотвращения memory leak"""
    now_ts = datetime.now(timezone.utc).timestamp()
    for ip in list(_login_attempts.keys()):
        _login_attempts[ip] = [
            t for t in _login_attempts[ip]
            if now_ts - t < _LOGIN_WINDOW_SECONDS
        ]
        if not _login_attempts[ip]:
            del _login_attempts[ip]

    # Очистка гостевых попыток участия
    cutoff = now_ts - GUEST_WINDOW_HOURS * 3600
    for ip in list(_guest_participation_attempts.keys()):
        _guest_participation_attempts[ip] = [
            t for t in _guest_participation_attempts[ip] if t > cutoff
        ]
        if not _guest_participation_attempts[ip]:
            del _guest_participation_attempts[ip]


# ─── App setup ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    scheduler.start()
    scheduler.add_job(check_upcoming_events, "interval", minutes=15, args=[app])
    # Добавить очистку login attempts каждые 30 минут
    scheduler.add_job(cleanup_login_attempts, "interval", minutes=30)
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan, title=PROJECT_NAME)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Rate limiter setup
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def format_datetime(value):
    """Format a datetime for display. Strips tz info so stored UTC naive datetimes
    display exactly as the user entered them (no offset shift)."""
    try:
        if not value:
            return "Дата не указана"
        if hasattr(value, "strftime"):
            # Strip tzinfo — display as-stored without UTC shift
            naive = value.replace(tzinfo=None) if value.tzinfo else value
            return naive.strftime("%d.%m.%Y в %H:%M")
        dt = datetime.fromisoformat(str(value))
        return dt.replace(tzinfo=None).strftime("%d.%m.%Y в %H:%M")
    except Exception:
        return str(value)


def _dt_for_input(value) -> str:
    """Format a datetime as YYYY-MM-DDTHH:MM for use in datetime-local inputs."""
    if not value:
        return ""
    try:
        dt = value if hasattr(value, "strftime") else datetime.fromisoformat(str(value))
        return dt.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M")
    except Exception:
        return str(value)


def event_type_icon(value):
    return {"friends": "👥", "date": "❤️", "colleagues": "💼", "other": "🎲"}.get(value, "📅")


templates.env.filters["datetime"] = format_datetime
templates.env.filters["event_type_icon"] = event_type_icon


# ─── Helper: safe redirect validation ─────────────────────────────────────────

def safe_redirect(url: str, allowed_paths: list[str] = None) -> RedirectResponse:
    """
    Validate redirect URL to prevent open redirect attacks.
    Only allows relative paths that start with / and match allowed patterns.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)

    # Block dangerous schemes (включая javascript:)
    if parsed.scheme and parsed.scheme not in ('', 'http', 'https'):
        raise HTTPException(status_code=400, detail="Invalid redirect")

    # Block absolute URLs (external redirects)
    if parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid redirect")

    # Only allow relative paths starting with /
    if not url.startswith('/'):
        raise HTTPException(status_code=400, detail="Invalid redirect")

    # Optional: whitelist specific path patterns
    if allowed_paths:
        path_without_query = url.split('?')[0]
        if not any(path_without_query.startswith(p) for p in allowed_paths):
            raise HTTPException(status_code=400, detail="Invalid redirect")

    return RedirectResponse(url, status_code=303)


# ─── Auth dependency ──────────────────────────────────────────────────────────

async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    user_id = request.cookies.get("user_id")
    if user_id:
        try:
            result = await db.execute(select(User).where(User.id == int(user_id)))
            return result.scalar_one_or_none()
        except (ValueError, Exception):
            return None
    return None


# ─── Helper: set confirmed_count on event objects ─────────────────────────────

async def set_confirmed_count(db: AsyncSession, events: list) -> None:
    """Оптимизированная версия - один запрос вместо N запросов"""
    if not events:
        return

    event_ids = [ev.id for ev in events]
    counts = await db.execute(
        select(EventParticipant.event_id, func.count())
        .where(EventParticipant.event_id.in_(event_ids))
        .group_by(EventParticipant.event_id)
    )
    count_map = {event_id: cnt for event_id, cnt in counts.all()}

    for ev in events:
        ev.confirmed_count = count_map.get(ev.id, 0)


# ─── Password validation ──────────────────────────────────────────────────────

# ─── Email existence check ────────────────────────────────────────────────────

# Known-good email domains
_ALLOWED_DOMAINS = {
    # Google
    "gmail.com",
    # Microsoft
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    # Mail.ru group
    "mail.ru", "inbox.ru", "bk.ru", "list.ru",
    # Yandex
    "yandex.ru", "yandex.com", "ya.ru",
    # Apple
    "icloud.com", "me.com", "mac.com",
    # Yahoo
    "yahoo.com", "yahoo.co.uk", "yahoo.fr", "yahoo.de",
    # ProtonMail
    "proton.me", "protonmail.com",
    # Rambler
    "rambler.ru", "ro.ru",
    # Others popular in RU/world
    "tutanota.com", "tuta.io", "gmx.com", "gmx.de",
    "fastmail.com", "zoho.com", "aol.com",
}


def verify_email_format(email: str) -> bool:
    """Check basic format and that the domain is from a known provider."""
    import re
    email = email.strip().lower()
    pattern = r"^[\w._%+\-]+@[\w.\-]+\.[a-zA-Z]{2,}$"
    if not re.match(pattern, email):
        return False
    domain = email.split("@")[1]
    return domain in _ALLOWED_DOMAINS


def is_strong_password(password: str) -> bool:
    return (
        len(password) >= 8
        and any(c.isdigit() for c in password)
        and any(c.islower() for c in password)
        and any(c.isupper() for c in password)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════════════════════════════════════



@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Return 204 No Content to silence browser favicon requests in logs."""
    from fastapi.responses import Response
    return Response(status_code=204)

@app.get("/")
async def home(
    request: Request,
    classic: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    events = []
    notifications_count = 0
    if current_user:
        stmt = (
            select(Event)
            .outerjoin(EventParticipant, EventParticipant.event_id == Event.id)
            .where(
                (Event.creator_id == current_user.id)
                | (EventParticipant.user_id == current_user.id)
            )
            .distinct()
            .order_by(Event.created_at.desc())
        )
        events = (await db.execute(stmt)).scalars().all()
        await set_confirmed_count(db, events)
        notifications_count = (
            await db.execute(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.user_id == current_user.id,
                    Notification.is_read == False,
                )
            )
        ).scalar_one()

    # Выбор шаблона в зависимости от параметра classic
    template_name = "index_classic.html" if classic == 1 else "index.html"

    return templates.TemplateResponse(
        template_name,
        {
            "request": request,
            "events": events,
            "project_name": PROJECT_NAME,
            "user": current_user,
            "notifications_count": notifications_count,
        },
    )


# ─── Auth ─────────────────────────────────────────────────────────────────────

@app.get("/login")
async def login_form(request: Request, error: str = None):
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": error, "project_name": PROJECT_NAME}
    )


@app.post("/login")
async def login(
    request: Request,
    name: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    # Brute-force guard
    client_ip = request.client.host if request.client else "unknown"
    now_ts = datetime.now(timezone.utc).timestamp()
    attempts = _login_attempts[client_ip]
    # Purge attempts outside the window
    _login_attempts[client_ip] = [t for t in attempts if now_ts - t < _LOGIN_WINDOW_SECONDS]
    if len(_login_attempts[client_ip]) >= _LOGIN_MAX_ATTEMPTS:
        return RedirectResponse("/login?error=too_many_attempts", status_code=303)
    _login_attempts[client_ip].append(now_ts)

    result = await db.execute(select(User).where(User.first_name == name))
    user = result.scalar_one_or_none()
    # BUG FIX: also guard against empty password_hash (guest accounts)
    if user and user.password_hash and check_password_hash(user.password_hash, password):
        response = RedirectResponse(url="/", status_code=303)
        # BUG FIX: add httponly flag
        response.set_cookie(key="user_id", value=str(user.id), httponly=True, samesite="lax", secure=IS_PRODUCTION, max_age=60*60*24*30)
        return response
    return RedirectResponse("/login?error=invalid_credentials", status_code=303)


@app.get("/register")
async def register_form(request: Request, error: str = None):
    return templates.TemplateResponse(
        "register.html", {"request": request, "error": error, "project_name": PROJECT_NAME}
    )


@app.post("/register")
@limiter.limit("5/minute")
async def register(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    if not is_strong_password(password):
        return RedirectResponse("/register?error=invalid_input", status_code=303)
    if not verify_email_format(email):
        return RedirectResponse("/register?error=invalid_input", status_code=303)
    if (await db.execute(select(User).where(User.first_name == name))).scalar_one_or_none():
        return RedirectResponse("/register?error=invalid_input", status_code=303)
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        return RedirectResponse("/register?error=invalid_input", status_code=303)

    # Remove any previous pending registration for this email
    await db.execute(
        delete(PendingRegistration).where(PendingRegistration.email == email)
    )

    code = "".join(random.choices("0123456789", k=4))
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    db.add(PendingRegistration(
        first_name=name,
        email=email,
        password_hash=generate_password_hash(password),
        code=code,
        expires_at=expires,
    ))
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        return RedirectResponse("/register?error=unknown", status_code=303)

    await send_email(
        subject="InSync — Подтверждение регистрации",
        body=(
            f"Здравствуйте, {name}!\n\n"
            f"Спасибо за регистрацию в InSync — платформе для организации встреч.\n\n"
            f"Ваш код подтверждения:\n\n"
            f"    {code}\n\n"
            f"Код действителен в течение 10 минут.\n\n"
            f"Если вы не регистрировались на нашей платформе, просто проигнорируйте это письмо.\n\n"
            f"С уважением,\n"
            f"Команда InSync"
        ),
        to_email=email,
        template="registration",
        template_vars={"name": name, "code": code}
    )

    # Вывод кода в консоль для удобства тестирования
    print(f"[DEMO] Код верификации для {email}: {code} (или используйте универсальный ключ: 084711)")

    return safe_redirect(f"/verify-email?email={email}", ["/verify-email"])


@app.get("/verify-email")
async def verify_email_form(request: Request, email: str = "", error: str = None):
    return templates.TemplateResponse("verify_email.html", {
        "request": request,
        "email": email,
        "error": error,
        "project_name": PROJECT_NAME,
    })


@app.post("/verify-email")
@limiter.limit("10/minute")
async def verify_email(
    request: Request,
    email: str = Form(...),
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    pending = (await db.execute(
        select(PendingRegistration).where(PendingRegistration.email == email)
    )).scalar_one_or_none()

    if not pending:
        return safe_redirect(f"/verify-email?email={email}&error=not_found", ["/verify-email"])
    # Ensure expires_at is timezone-aware for comparison
    expires_at = pending.expires_at.replace(tzinfo=timezone.utc) if pending.expires_at.tzinfo is None else pending.expires_at
    if datetime.now(timezone.utc) > expires_at:
        await db.delete(pending)
        await db.commit()
        return safe_redirect(f"/verify-email?email={email}&error=expired", ["/verify-email"])

    # Проверка кода: либо правильный код, либо универсальный ключ 084711
    if pending.code != code.strip() and code.strip() != "084711":
        return safe_redirect(f"/verify-email?email={email}&error=wrong_code", ["/verify-email"])

    # Code correct — create the real user
    new_user = User(
        first_name=pending.first_name,
        email=pending.email,
        password_hash=pending.password_hash,
    )
    db.add(new_user)
    await db.delete(pending)
    try:
        await db.commit()
        await db.refresh(new_user)
    except Exception:
        await db.rollback()
        return RedirectResponse("/register?error=unknown", status_code=303)

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(key="user_id", value=str(new_user.id), httponly=True, samesite="lax", secure=IS_PRODUCTION, max_age=60*60*24*30)
    return response


@app.post("/verify-email/resend")
@limiter.limit("3/minute")
async def resend_verification(
    request: Request,
    email: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    pending = (await db.execute(
        select(PendingRegistration).where(PendingRegistration.email == email)
    )).scalar_one_or_none()
    if not pending:
        return safe_redirect(f"/verify-email?email={email}&error=not_found", ["/verify-email"])

    code = "".join(random.choices("0123456789", k=4))
    pending.code = code
    pending.expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    await db.commit()

    await send_email(
        subject="InSync — новый код подтверждения",
        body=(
            f"Привет, {pending.first_name}!\n\n"
            f"Ваш новый код подтверждения: {code}\n\n"
            f"Код действителен 10 минут."
        ),
        to_email=email,
        template="registration",
        template_vars={"name": pending.first_name, "code": code}
    )
    return safe_redirect(f"/verify-email?email={email}&error=resent", ["/verify-email"])


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("user_id")
    return response


# ─── Create / Edit / Delete Events ────────────────────────────────────────────

@app.get("/create")
async def create_form(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user),
):
    # BUG FIX: redirect unauthenticated users immediately
    if not current_user:
        return RedirectResponse("/login", status_code=303)
    today = datetime.now().date().isoformat()
    return templates.TemplateResponse(
        "create.html",
        {"request": request, "today": today, "emoji_list": EMOJI_LIST},
    )


@app.post("/create")
async def create_event(
    request: Request,
    title: str = Form(...),
    description: str = Form(None),
    event_type: str = Form(...),
    max_participants: int = Form(...),
    location: str = Form(...),
    date: str = Form(...),
    icon_emoji: str = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    # BUG FIX: auth check BEFORE date validation (don't leak processing to guests)
    if not current_user:
        return RedirectResponse("/login", status_code=303)

    today = datetime.now().date().isoformat()
    form_ctx = dict(
        request=request,
        today=today,
        emoji_list=EMOJI_LIST,
        title=title,
        description=description,
        event_type=event_type,
        max_participants=max_participants,
        location=location,
        date=date,
        icon_emoji=icon_emoji,
    )

    try:
        # FIX: attach UTC so asyncpg stores exactly what the user typed, no local-tz shift
        event_date = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    except ValueError:
        return templates.TemplateResponse("create.html", {**form_ctx, "error": "invalid_date"})

    if event_date < datetime.now(timezone.utc):
        return templates.TemplateResponse("create.html", {**form_ctx, "error": "past_date"})

    # Генерация уникального кода с проверкой
    while True:
        code = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ123456789", k=10))
        existing = await db.execute(select(Event).where(Event.invite_code == code))
        if not existing.scalar_one_or_none():
            break

    db.add(
        Event(
            creator_id=current_user.id,
            title=title,
            description=description,
            type=event_type,
            max_participants=max_participants,
            planned_datetime=event_date,
            location=location,
            invite_code=code,
            icon_emoji=icon_emoji,
        )
    )
    await db.commit()
    return RedirectResponse("/", status_code=303)


@app.get("/event/{event_id}")
async def event_by_id(event_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Событие не найдено")
    return RedirectResponse(f"/join/{event.invite_code}", status_code=303)


@app.get("/event/{event_id}/edit")
async def edit_form(
    request: Request,
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    # BUG FIX: auth + ownership check was missing on the GET handler
    if not current_user:
        return RedirectResponse("/login", status_code=303)
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404)
    if event.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Нет прав на редактирование")

    today = datetime.now().date().isoformat()
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "event": event,
            "today": today,
            "emoji_list": EMOJI_LIST,
            "project_name": PROJECT_NAME,
            "current_user": current_user,
            "event_dt_local": _dt_for_input(event.planned_datetime),
        },
    )


@app.post("/event/{event_id}/edit")
async def edit_event(
    request: Request,
    event_id: int,
    title: str = Form(...),
    description: str = Form(None),
    event_type: str = Form(...),
    max_participants: int = Form(...),
    location: str = Form(...),
    date: str = Form(...),
    icon_emoji: str = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    if not current_user:
        return RedirectResponse("/login", status_code=303)

    event = (
        await db.execute(select(Event).where(Event.id == event_id))
    ).scalar_one_or_none()
    if not event or event.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Нет прав на редактирование")

    today = datetime.now().date().isoformat()
    form_ctx = dict(
        request=request,
        event=event,
        today=today,
        emoji_list=EMOJI_LIST,
        project_name=PROJECT_NAME,
        current_user=current_user,
        event_dt_local=_dt_for_input(event.planned_datetime),
        form_data={
            "title": title,
            "description": description,
            "event_type": event_type,
            "max_participants": max_participants,
            "location": location,
            "date": date,
            "icon_emoji": icon_emoji,
        },
    )

    try:
        # FIX: attach UTC so asyncpg stores exactly what the user typed, no local-tz shift
        event_date = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    except ValueError:
        return templates.TemplateResponse("edit.html", {**form_ctx, "error": "invalid_date"})

    if event_date < datetime.now(timezone.utc):
        return templates.TemplateResponse("edit.html", {**form_ctx, "error": "past_date"})

    event.title = title
    event.description = description
    event.type = event_type
    event.max_participants = max_participants
    event.location = location
    event.planned_datetime = event_date
    event.icon_emoji = icon_emoji
    await db.commit()
    return RedirectResponse(f"/profile/{current_user.id}", status_code=303)


@app.post("/event/{event_id}/delete")
async def delete_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    if not current_user:
        return RedirectResponse("/login", status_code=303)
    event = (await db.execute(select(Event).where(Event.id == event_id))).scalar_one_or_none()
    if not event or event.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Нет прав на удаление")
    await db.delete(event)
    await db.commit()
    return RedirectResponse(f"/profile/{current_user.id}", status_code=303)


@app.post("/event/{event_id}/reset-code")
async def reset_event_code(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(status_code=403, detail="Требуется авторизация")
    event = (await db.execute(select(Event).where(Event.id == event_id))).scalar_one_or_none()
    if not event or event.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Нет прав на изменение кода")

    # Generate new unique code
    while True:
        new_code = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ123456789", k=10))
        existing = await db.execute(select(Event).where(Event.invite_code == new_code))
        if not existing.scalar_one_or_none():
            break

    event.invite_code = new_code

    # Organiser gets the new code in their notification
    db.add(
        Notification(
            user_id=current_user.id,
            message=f"🔑 Вы сбросили код к «{event.title}». Новый код: {new_code}",
            is_read=False,
        )
    )
    # Participants only get a heads-up that the code changed — no new code exposed
    participants = await db.execute(
        select(User)
        .join(EventParticipant, EventParticipant.user_id == User.id)
        .where(
            EventParticipant.event_id == event.id,
            EventParticipant.user_id.isnot(None),
            User.id != current_user.id,
        )
    )

    # Bulk insert вместо цикла
    notifications = [
        Notification(
            user_id=p.id,
            message=f"⚠️ Организатор изменил код доступа к «{event.title}». Уточните новый код у организатора.",
            is_read=False,
        )
        for p in participants.scalars().all()
    ]
    db.add_all(notifications)
    await db.commit()
    return RedirectResponse(f"/join/{new_code}", status_code=303)


# ─── Join / Participate ────────────────────────────────────────────────────────

@app.get("/join")
async def join_form(request: Request):
    return templates.TemplateResponse("join.html", {"request": request})


@app.post("/join")
async def join_post(invite_code: str = Form(...)):
    return RedirectResponse(f"/join/{invite_code.strip().upper()}", status_code=303)


@app.get("/join/{invite_code}")
async def event_page(
    request: Request,
    invite_code: str,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    result = await db.execute(select(Event).where(Event.invite_code == invite_code))
    event = result.scalar_one_or_none()
    if not event:
        return RedirectResponse("/join?err=1", status_code=303)

    cnt = await db.execute(
        select(func.count())
        .select_from(EventParticipant)
        .where(EventParticipant.event_id == event.id)
    )
    event.confirmed_count = cnt.scalar_one()

    # BUG FIX: handle both registered users (join via User) and guests (guest_name column)
    ep_rows = await db.execute(
        select(EventParticipant, User)
        .outerjoin(User, EventParticipant.user_id == User.id)
        .where(EventParticipant.event_id == event.id)
    )
    rows = ep_rows.all()
    participant_names   = [u.first_name if u else ep.guest_name for ep, u in rows]
    participant_avatars = [u.avatar     if u else None           for ep, u in rows]
    participant_ids     = [u.id         if u else None           for ep, u in rows]

    is_participant = False
    if current_user:
        check = await db.execute(
            select(EventParticipant).where(
                EventParticipant.event_id == event.id,
                EventParticipant.user_id == current_user.id,
            )
        )
        is_participant = check.scalar_one_or_none() is not None

    return templates.TemplateResponse(
        "event.html",
        {
            "request": request,
            "event": event,
            "participant_names": participant_names,
            "participant_avatars": participant_avatars,
            "participant_ids": participant_ids,
            "is_participant": is_participant,
            "user": current_user,
            "project_name": PROJECT_NAME,
        },
    )


@app.post("/join/{invite_code}")
async def confirm_participation(
    reuqest: Request,
    invite_code: str,
    guest_name: str = Form(""),
    # BUG FIX: "join on behalf of another" checkbox — if True, even a logged-in
    # user joins as a named guest rather than as themselves.
    join_as_other: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    # Rate limit for guests (max 5 participations per IP per 24 hours)
    if not current_user:
        client_ip = request.client.host if request.client else "unknown"
        now_ts = datetime.now(timezone.utc).timestamp()
        attempts = _guest_participation_attempts[client_ip]
        cutoff = now_ts - GUEST_WINDOW_HOURS * 3600
        _guest_participation_attempts[client_ip] = [t for t in attempts if t > cutoff]
        if len(_guest_participation_attempts[client_ip]) >= MAX_GUEST_PARTICIPATIONS:
            return RedirectResponse(f"/join/{invite_code}?error=guest_limit", status_code=303)
        _guest_participation_attempts[client_ip].append(now_ts)

    event = (
        await db.execute(select(Event).where(Event.invite_code == invite_code))
    ).scalar_one_or_none()
    if not event:
        return RedirectResponse("/join?err=1", status_code=303)

    # Race condition fix: lock the event row with FOR UPDATE
    async with db.begin_nested():
        # Lock the event row to prevent concurrent modifications
        locked_event = (
            await db.execute(
                select(Event).where(Event.id == event.id).with_for_update()
            )
        ).scalar_one()

        # Now count participants (no FOR UPDATE on aggregate)
        cnt = await db.execute(
            select(func.count())
            .select_from(EventParticipant)
            .where(EventParticipant.event_id == event.id)
        )
        if cnt.scalar_one() >= event.max_participants:
            return RedirectResponse(f"/join/{invite_code}?error=full", status_code=303)

    # Decide path: logged-in user joining as themselves vs guest (anonymous or on-behalf)
    use_guest_path = (not current_user) or join_as_other

    if not use_guest_path:
        # ── Registered user joining as themselves ─────────────────────────────
        existing = await db.execute(
            select(EventParticipant).where(
                EventParticipant.event_id == event.id,
                EventParticipant.user_id == current_user.id,
            )
        )
        if existing.scalar_one_or_none():
            return RedirectResponse(f"/join/{invite_code}?error=already_joined", status_code=303)

        db.add(EventParticipant(event_id=event.id, user_id=current_user.id, guest_name=None))
        db.add(Notification(
            user_id=current_user.id,
            message=f"Вы подтвердили участие в «{event.title}»",
            is_read=False,
        ))
        display_name = current_user.first_name
    else:
        # ── Guest path (anonymous or logged-in user joining on behalf of someone) ──
        # BUG FIX: guests stored as guest_name in EventParticipant — no User row.
        # BUG FIX: when join_as_other is set, use the typed name, not current_user.
        name = guest_name.strip()[:50]  # limit guest name length
        if not name:
            return RedirectResponse(f"/join/{invite_code}?error=empty_name", status_code=303)

        # Guest name already taken on this event
        existing_guest = await db.execute(
            select(EventParticipant).where(
                EventParticipant.event_id == event.id,
                EventParticipant.guest_name == name,
            )
        )
        if existing_guest.scalar_one_or_none():
            return RedirectResponse(f"/join/{invite_code}?error=already_joined", status_code=303)

        # Prevent collision with a registered user already in the event
        registered = (
            await db.execute(select(User).where(User.first_name == name))
        ).scalar_one_or_none()
        if registered:
            reg_part = await db.execute(
                select(EventParticipant).where(
                    EventParticipant.event_id == event.id,
                    EventParticipant.user_id == registered.id,
                )
            )
            if reg_part.scalar_one_or_none():
                return RedirectResponse(f"/join/{invite_code}?error=already_joined", status_code=303)

        db.add(EventParticipant(event_id=event.id, user_id=None, guest_name=name))
        display_name = name

    db.add(Notification(
        user_id=event.creator_id,
        message=f"«{display_name}» подтвердил участие в «{event.title}»",
        is_read=False,
    ))
    await db.commit()
    return RedirectResponse(f"/join/{invite_code}", status_code=303)


@app.post("/join/{invite_code}/withdraw")
async def withdraw_participation(
    invite_code: str,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    if not current_user:
        return RedirectResponse("/login", status_code=303)
    event = (
        await db.execute(select(Event).where(Event.invite_code == invite_code))
    ).scalar_one_or_none()
    if not event:
        return RedirectResponse("/join?err=1", status_code=303)

    await db.execute(
        delete(EventParticipant).where(
            EventParticipant.event_id == event.id,
            EventParticipant.user_id == current_user.id,
        )
    )
    db.add(
        Notification(
            user_id=event.creator_id,
            message=f"{current_user.first_name} отменил участие в «{event.title}»",
            is_read=False,
        )
    )
    db.add(
        Notification(
            user_id=current_user.id,
            message=f"Вы отменили участие в «{event.title}»",
            is_read=False,
        )
    )
    await db.commit()
    return RedirectResponse(f"/join/{invite_code}", status_code=303)


# ─── Profile ───────────────────────────────────────────────────────────────────

@app.get("/profile/{user_id}")
async def profile(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    target_user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    is_owner = current_user is not None and current_user.id == user_id

    created_events = (
        await db.execute(
            select(Event)
            .where(Event.creator_id == user_id)
            .order_by(Event.planned_datetime.desc())
        )
    ).scalars().all()
    await set_confirmed_count(db, created_events)

    # BUG FIX: filter user_id IS NOT NULL to exclude guest-only participations
    participated_events = (
        await db.execute(
            select(Event)
            .join(EventParticipant, EventParticipant.event_id == Event.id)
            .where(
                EventParticipant.user_id == user_id,
                EventParticipant.user_id.isnot(None),
            )
            .order_by(Event.planned_datetime.desc())
        )
    ).scalars().all()
    await set_confirmed_count(db, participated_events)

    notifications_count = 0
    if is_owner:
        notifications_count = (
            await db.execute(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.user_id == current_user.id,
                    Notification.is_read == False,
                )
            )
        ).scalar_one()

    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "user": target_user,
            "created_events": created_events,
            "participated_events": participated_events,
            "is_owner": is_owner,
            "created_count": len(created_events),
            "participated_count": len(participated_events),
            "total_guests": sum(ev.confirmed_count for ev in created_events),
            "notifications_count": notifications_count,
            "project_name": PROJECT_NAME,
        },
    )


@app.post("/profile/avatar")
async def upload_avatar(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(status_code=403, detail="Требуется авторизация")
    ALLOWED_IMG_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    ext = os.path.splitext(file.filename or "avatar.jpg")[1].lower()
    if ext not in ALLOWED_IMG_EXTENSIONS:
        return RedirectResponse(f"/profile/{current_user.id}?error=invalid_file", status_code=303)

    # BUG FIX: use async read() instead of sync seek/tell on SpooledTemporaryFile
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        return RedirectResponse(f"/profile/{current_user.id}?error=file_too_large", status_code=303)

    # Проверка реального формата файла (magic bytes)
    img_type = imghdr.what(None, h=content)
    if img_type not in ('jpeg', 'png', 'gif', 'webp'):
        return RedirectResponse(f"/profile/{current_user.id}?error=invalid_file", status_code=303)

    ext = os.path.splitext(file.filename or "avatar.jpg")[1].lower() or ".jpg"
    # Sanitize: only allow alphanumeric extension chars to prevent path traversal
    import re as _re
    ext = ext if _re.match(r"^\.[a-z0-9]+$", ext) else ".jpg"
    filename = f"{__import__('uuid').uuid4().hex}{ext}"
    filepath = os.path.join(AVATAR_DIR, filename)

    # Асинхронная запись файла
    async with aiofiles.open(filepath, "wb") as f:
        await f.write(content)

    # Асинхронное удаление старого аватара
    if current_user.avatar:
        old_path = os.path.join(AVATAR_DIR, os.path.basename(current_user.avatar))
        if await aiofiles.os.path.exists(old_path):
            await aiofiles.os.remove(old_path)

    current_user.avatar = f"/static/avatars/{filename}"
    await db.commit()
    return RedirectResponse(f"/profile/{current_user.id}", status_code=303)


@app.post("/profile/avatar/delete")
async def delete_avatar(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(status_code=403, detail="Требуется авторизация")
    if current_user.avatar:
        old_path = os.path.join(AVATAR_DIR, os.path.basename(current_user.avatar))
        if await aiofiles.os.path.exists(old_path):
            await aiofiles.os.remove(old_path)
        current_user.avatar = None
        await db.commit()
    return RedirectResponse(f"/profile/{current_user.id}", status_code=303)


@app.get("/profile/{user_id}/settings")
async def settings_form(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    if not current_user or current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    return templates.TemplateResponse(
        "settings.html", {
            "request": request,
            "user": user,
            "project_name": PROJECT_NAME,
            "reminder_choices": REMINDER_CHOICES,
            "reminder_labels": REMINDER_LABELS,
        }
    )


@app.post("/profile/{user_id}/settings")
async def update_settings(
    user_id: int,
    first_name: str = Form(...),
    email: str = Form(...),
    reminder_minutes_before: int = Form(60),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    if not current_user or current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    if (
        await db.execute(
            select(User).where(User.first_name == first_name, User.id != user_id)
        )
    ).scalar_one_or_none():
        return RedirectResponse(f"/profile/{user_id}/settings?error=name_taken", status_code=303)
    if (
        await db.execute(select(User).where(User.email == email, User.id != user_id))
    ).scalar_one_or_none():
        return RedirectResponse(f"/profile/{user_id}/settings?error=email_taken", status_code=303)
    # Only re-verify if the email address actually changed
    if not verify_email_format(email):
        return RedirectResponse(f"/profile/{user_id}/settings?error=email_invalid", status_code=303)

    # Clamp slider value to a sensible range
    # Only accept values from the predefined choices to prevent arbitrary values
    if reminder_minutes_before not in REMINDER_CHOICES:
        reminder_minutes_before = 60  # default to 1 hour

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    user.first_name = first_name
    user.email = email
    user.reminder_minutes_before = reminder_minutes_before
    await db.commit()
    return RedirectResponse(f"/profile/{user_id}/settings?success=updated", status_code=303)


@app.post("/profile/{user_id}/change-password")
async def change_password(
    user_id: int,
    old_password: str = Form(...),
    new_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    if not current_user or current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    if not check_password_hash(user.password_hash, old_password):
        return RedirectResponse(f"/profile/{user_id}/settings?error=wrong_password", status_code=303)
    # BUG FIX: validate new password strength server-side
    if not is_strong_password(new_password):
        return RedirectResponse(f"/profile/{user_id}/settings?error=weak_password", status_code=303)
    user.password_hash = generate_password_hash(new_password)
    await db.commit()
    return RedirectResponse(f"/profile/{user_id}/settings?success=password_changed", status_code=303)


# ─── Standalone forgot-password (email-based) ────────────────────────────────

@app.get("/forgot-password")
async def forgot_password_form(request: Request, error: str = None):
    return templates.TemplateResponse("forgot_password.html", {
        "request": request, "error": error, "project_name": PROJECT_NAME,
    })


@app.post("/forgot-password")
@limiter.limit("3/minute")
async def forgot_password_send(
    request: Request,
    email: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    # Always redirect with success to avoid email enumeration
    if user:
        # Invalidate any previous tokens for this user
        await db.execute(
            delete(PasswordReset).where(PasswordReset.user_id == user.id)
        )
        token = secrets.token_urlsafe(48)
        expires = datetime.now(timezone.utc) + timedelta(minutes=30)
        db.add(PasswordReset(user_id=user.id, token=token, expires_at=expires))
        await db.commit()

        await send_email(
            subject="InSync — сброс пароля",
            body=(
                f"Привет, {user.first_name}!\n\n"
                f"Для сброса пароля перейдите по ссылке:\n"
                f"{APP_URL}/reset-password?token={token}\n\n"
                f"Ссылка действительна 30 минут.\n"
                f"Если вы не запрашивали сброс — проигнорируйте это письмо."
            ),
            to_email=email,
            template="password_reset",
            template_vars={"name": user.first_name, "reset_link": f"{APP_URL}/reset-password?token={token}"}
        )
    return RedirectResponse("/forgot-password?error=sent", status_code=303)


@app.get("/reset-password")
async def reset_password_form(request: Request, token: str = "", error: str = None):
    return templates.TemplateResponse("reset_password.html", {
        "request": request, "token": token, "error": error, "project_name": PROJECT_NAME,
    })


@app.post("/reset-password")
async def reset_password_submit(
    token: str = Form(...),
    new_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    reset = (await db.execute(
        select(PasswordReset).where(PasswordReset.token == token, PasswordReset.used == False)
    )).scalar_one_or_none()

    if not reset:
        return RedirectResponse(f"/reset-password?token={token}&error=invalid", status_code=303)
    # Ensure expires_at is timezone-aware for comparison
    expires_at = reset.expires_at.replace(tzinfo=timezone.utc) if reset.expires_at.tzinfo is None else reset.expires_at
    if datetime.now(timezone.utc) > expires_at:
        reset.used = True
        await db.commit()
        return RedirectResponse(f"/reset-password?token={token}&error=expired", status_code=303)
    if not is_strong_password(new_password):
        return RedirectResponse(f"/reset-password?token={token}&error=weak", status_code=303)

    user = (await db.execute(select(User).where(User.id == reset.user_id))).scalar_one_or_none()
    if not user:
        return RedirectResponse("/login", status_code=303)

    user.password_hash = generate_password_hash(new_password)
    reset.used = True
    await db.commit()
    return RedirectResponse("/login?success=password_reset", status_code=303)


# ─── Delete account ───────────────────────────────────────────────────────────

@app.post("/profile/{user_id}/delete")
async def delete_account(
    user_id: int,
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    if not current_user or current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    if not check_password_hash(current_user.password_hash, password):
        return RedirectResponse(f"/profile/{user_id}/settings?error=wrong_password_delete", status_code=303)

    await db.delete(current_user)
    await db.commit()
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("user_id")
    return response


# ─── Legacy settings forgot-password (kept for backward compat, redirects) ───

@app.post("/profile/{user_id}/forgot-password")
async def forgot_password_legacy(user_id: int):
    return RedirectResponse("/forgot-password", status_code=303)


@app.get("/notifications")
async def get_notifications(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    if not current_user:
        return RedirectResponse("/login", status_code=303)
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
    )
    return templates.TemplateResponse(
        "notifications.html",
        {
            "request": request,
            "notifications": result.scalars().all(),
            "project_name": PROJECT_NAME,
        },
    )


@app.post("/notifications/clear")
async def clear_notifications(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    if not current_user:
        return {"status": "error"}
    await db.execute(
        delete(Notification).where(Notification.user_id == current_user.id)
    )
    await db.commit()
    return RedirectResponse("/notifications", status_code=303)
@app.post("/notifications/{notif_id}/read")
async def mark_notification_read(
    notif_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    if not current_user:
        return {"status": "error"}
    notif = (
        await db.execute(
            select(Notification).where(
                Notification.id == notif_id,
                Notification.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if notif:
        notif.is_read = True
        await db.commit()
    return {"status": "ok"}


# ─── Admin ─────────────────────────────────────────────────────────────────────

@app.get("/admin/complaints")
async def admin_complaints_page(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user),
):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    complaints = load_complaints()
    # Show unresolved first, then sort by timestamp descending
    complaints.sort(key=lambda c: (c.get("resolved", False), c.get("timestamp", "")))
    return templates.TemplateResponse(
        "admin_complaints.html",
        {"request": request, "complaints": complaints, "project_name": PROJECT_NAME},
    )


@app.post("/admin/complaints/{complaint_id}/resolve")
async def resolve_complaint(
    complaint_id: str,
    current_user: Optional[User] = Depends(get_current_user),
):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    complaints = load_complaints()
    for c in complaints:
        if c.get("id") == complaint_id:
            c["resolved"] = True
            break
    save_complaints(complaints)
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
