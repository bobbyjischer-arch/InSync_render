from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

REMINDER_CHOICES = [15, 30, 60, 120, 180, 360, 720, 1440]
REMINDER_LABELS = {
    15: "За 15 минут", 30: "За 30 минут", 60: "За 1 час",
    120: "За 2 часа", 180: "За 3 часа", 360: "За 6 часов",
    720: "За 12 часов", 1440: "За 1 день",
}


class User(Base):
    __tablename__ = "users"
    id                      = Column(Integer, primary_key=True, index=True)
    first_name              = Column(String, unique=True, nullable=False)
    email                   = Column(String, unique=True, nullable=False)
    password_hash           = Column(String, nullable=False)
    created_at              = Column(DateTime(timezone=True), server_default=func.now())
    avatar                  = Column(String, nullable=True)
    reminder_minutes_before = Column(Integer, nullable=False, server_default="60")
    is_admin                = Column(Boolean, nullable=False, server_default="false")


class Event(Base):
    __tablename__ = "events"
    id               = Column(Integer, primary_key=True, index=True)
    creator_id       = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    title            = Column(String, nullable=False)
    description      = Column(Text)
    type             = Column(String)
    max_participants = Column(Integer)
    planned_datetime = Column(DateTime(timezone=True))
    location         = Column(String)
    invite_code      = Column(String, unique=True, nullable=False)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())
    icon_emoji       = Column(String)


class EventParticipant(Base):
    __tablename__ = "event_participants"
    id         = Column(Integer, primary_key=True, index=True)
    event_id   = Column(Integer, ForeignKey("events.id",  ondelete="CASCADE"), nullable=False)
    user_id    = Column(Integer, ForeignKey("users.id",   ondelete="CASCADE"), nullable=True)
    guest_name = Column(String, nullable=True)
    joined_at  = Column(DateTime(timezone=True), server_default=func.now())
    reminder_sent = Column(Boolean, default=False)
    __table_args__ = (
        UniqueConstraint("event_id", "user_id",    name="uq_event_user"),
        UniqueConstraint("event_id", "guest_name", name="uq_event_guest"),
    )


class Notification(Base):
    __tablename__ = "notifications"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    message    = Column(Text, nullable=False)
    is_read    = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PendingRegistration(Base):
    """
    Stores a registration that is awaiting email verification.
    Deleted once the user confirms the code or it expires (10 min).
    """
    __tablename__ = "pending_registrations"
    id            = Column(Integer, primary_key=True, index=True)
    first_name    = Column(String, nullable=False)
    email         = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    code          = Column(String(6), nullable=False)   # 6-digit OTP
    expires_at    = Column(DateTime(timezone=True), nullable=False)


class PasswordReset(Base):
    """
    One-time token sent to the user's email for password reset.
    Expires after 30 minutes.
    """
    __tablename__ = "password_resets"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token      = Column(String(64), unique=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used       = Column(Boolean, default=False)
