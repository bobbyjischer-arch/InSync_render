import os
import sys
import base64
from dotenv import load_dotenv

# Load .env from the same directory as this file — works regardless of cwd
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_ENV_PATH)

PROJECT_NAME = os.getenv("PROJECT_NAME", "InSync")
APP_URL       = os.getenv("APP_URL", "http://localhost:8000")  # set to https://your-domain.com in .env
DATABASE_URL  = os.getenv("DATABASE_URL", "sqlite:///insync.db")
IS_PRODUCTION = os.getenv("ENVIRONMENT", "development") == "production"

# ── Encryption key ────────────────────────────────────────────────────────────
_DEFAULT_AES_KEY = "VSW2zcBKkbRQJYFBaUVC5eqtF8qtH2yyfj01tFEwcBI="

AES_KEY = os.getenv("AES_KEY", _DEFAULT_AES_KEY)

# Validate key format eagerly so startup fails clearly instead of at first write
try:
    _decoded = base64.urlsafe_b64decode(AES_KEY.encode())
    if len(_decoded) != 32:
        raise ValueError("AES_KEY must decode to exactly 32 bytes (256 bits).")
except Exception as exc:
    print(f"[config] FATAL: invalid AES_KEY — {exc}", file=sys.stderr)
    sys.exit(1)

# Warn loudly if the default key is still in use
if AES_KEY == _DEFAULT_AES_KEY:
    print(
        "[config] WARNING: using the default AES_KEY from source code. "
        "Set a unique AES_KEY in your .env file before going to production. "
        "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"",
        file=sys.stderr,
    )

DEFAULT_USER_ID = None

# ── SMTP ──────────────────────────────────────────────────────────────────────
SMTP_SERVER   = os.getenv("SMTP_SERVER",   "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER",     "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
ADMIN_EMAIL   = os.getenv("ADMIN_EMAIL",   "")
#Needs to be fixed after release

