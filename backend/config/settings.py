"""Django sozlamalari — Million Piksel."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR.parent / ".env")


def env(key, default=None):
    return os.environ.get(key, default)


def env_bool(key, default=False):
    return str(env(key, str(default))).lower() in ("1", "true", "yes", "on")


SECRET_KEY = env("SECRET_KEY", "dev-only-insecure-key")
DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = [h for h in env("ALLOWED_HOSTS", "*").split(",") if h]
CSRF_TRUSTED_ORIGINS = [
    o for o in env("CSRF_TRUSTED_ORIGINS", "").split(",") if o
]

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "canvas",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"
WSGI_APPLICATION = None

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# PostgreSQL — ishlab chiqarish uchun. USE_SQLITE=1 bo'lsa, lokal sinov uchun
# SQLite ishlatiladi (Windows'da Postgres o'rnatmasdan sinab ko'rish uchun).
USE_SQLITE = env_bool("USE_SQLITE", False)

if USE_SQLITE:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "dev.sqlite3",
            "OPTIONS": {"timeout": 20, "init_command": "PRAGMA journal_mode=WAL;"},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", "millionpixel"),
            "USER": env("POSTGRES_USER", "mp"),
            "PASSWORD": env("POSTGRES_PASSWORD", "mp"),
            "HOST": env("POSTGRES_HOST", "127.0.0.1"),
            "PORT": env("POSTGRES_PORT", "5432"),
            # CONN_MAX_AGE va pool BIRGA ishlamaydi — Django
            # "Pooling doesn't support persistent connections" deb
            # ishga tushmaydi. Pool allaqachon ulanishlarni qayta
            # ishlatadi, ya'ni CONN_MAX_AGE keraksiz.
            "CONN_MAX_AGE": 0,
            # Har bir protsess o'z poolini ochadi: 2 web worker +
            # history + timelapse = 4 x 8 = 32 ulanish, postgres'dagi
            # max_connections=50 ga sig'adi.
            "OPTIONS": {"pool": {"min_size": 1, "max_size": 8}},
        }
    }

AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "uz"
TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR.parent / "frontend"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        # manifest_strict=False — collectstatic unutilsa sayt yiqilmaydi
        "BACKEND": "config.storage.ForgivingManifestStaticFilesStorage"
        if not DEBUG
        else "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------- loyiha
REDIS_URL = env("REDIS_URL", "redis://127.0.0.1:6379/0")

# FAKE_REDIS=1 — protsess ichidagi Redis emulyatsiyasi (Lua bilan).
# FAQAT lokal sinov uchun: bitta worker, ma'lumot saqlanmaydi.
FAKE_REDIS = env_bool("FAKE_REDIS", False)

CANVAS_SIZE = int(env("CANVAS_SIZE", 1000))          # 1000 x 1000
MAX_ENERGY = int(env("MAX_ENERGY", 50))              # zaxira
PALETTE_LEN = 24                                     # frontend bilan bir xil

# Broadcast — 1000+ foydalanuvchida hayotiy muhim.
BROADCAST_INTERVAL_MS = int(env("BROADCAST_INTERVAL_MS", 80))
ONLINE_TTL_SEC = int(env("ONLINE_TTL_SEC", 45))      # yozuv yangilanmasa — offline
ONLINE_REFRESH_MS = int(env("ONLINE_REFRESH_MS", 2000))

# Snapshot gzip keshi. Kuchsiz protsessorda eng muhim sozlama:
# 1 MB ni siqish ~20 ms CPU. Kesh bo'lmasa, ko'p odam bir vaqtda kirganda
# yadro shunga ketadi. Qiymatni oshirsangiz CPU tejaladi, lekin yangi
# kelgan foydalanuvchi biroz eskirgan doskani oladi (WS uni tezda tuzatadi).
SNAPSHOT_CACHE_SEC = float(env("SNAPSHOT_CACHE_SEC", 1.0))

# ---------------------------------------------------------------- do'st chaqirish
# Chaqirilgan do'st haqiqatan kirsa, chaqirgan odamning zaxirasi oshadi.
INVITE_BONUS_ENERGY = int(env("INVITE_BONUS_ENERGY", 5))    # har do'st uchun
# Bonus chaqirilgan odam shuncha piksel qo'ygandan keyin beriladi.
# IP tekshiruvi o'rniga ishlatiladi — sabablari canvas/invites.py da.
INVITE_MIN_PIXELS = int(env("INVITE_MIN_PIXELS", 20))
INVITE_BONUS_MAX = int(env("INVITE_BONUS_MAX", 50))         # jami chegara
TEAM_MAX_MEMBERS = int(env("TEAM_MAX_MEMBERS", 50))

# ---------------------------------------------------------------- timelapse
MEDIA_ROOT = Path(env("MEDIA_ROOT", BASE_DIR / "media"))
MEDIA_URL = "/media/"
TIMELAPSE_DIR = MEDIA_ROOT / "timelapse"

# Suratlar oralig'i FAOLLIKKA qarab o'zgaradi: doska tez chizilayotganda
# tez-tez, jimjitlikda kamdan-kam. Aks holda yo lavhalar yo'qoladi,
# yo minglab bir xil kadr yig'ilib qoladi.
TIMELAPSE_MIN_SEC = int(env("TIMELAPSE_MIN_SEC", 30))       # eng tez
TIMELAPSE_MAX_SEC = int(env("TIMELAPSE_MAX_SEC", 1800))     # eng sekin
TIMELAPSE_BUSY_PIXELS = int(env("TIMELAPSE_BUSY_PIXELS", 2000))
TIMELAPSE_KEEP_DAYS = int(env("TIMELAPSE_KEEP_DAYS", 0))    # 0 = cheksiz saqlash

# DEV_LOGIN=1 — parolsiz "dasturchi sifatida kirish" tugmasi.
# Faqat sinov uchun. Yoqilgan bo'lsa, HAR KIM hisob yarata oladi.
# Ishlab chiqarishda 0 bo'lishi SHART.
DEV_LOGIN = env_bool("DEV_LOGIN", False)

TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", "")
# Chaqiruv havolasi uchun: https://t.me/<username>/app?startapp=KOD
TELEGRAM_BOT_USERNAME = env("TELEGRAM_BOT_USERNAME", "").lstrip("@")
GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", "")
JWT_SECRET = env("JWT_SECRET", SECRET_KEY)
JWT_TTL_DAYS = int(env("JWT_TTL_DAYS", 30))

# Nginx/Cloudflare orqasida haqiqiy IP
REAL_IP_HEADER = env("REAL_IP_HEADER", "HTTP_X_FORWARDED_FOR")

# ---------------------------------------------------------------- xavfsizlik
# TLS ni nginx uzadi, lekin Django buni bilishi kerak — aks holda u
# so'rovni http deb hisoblaydi va cookie'larni himoyasiz yuboradi.
#
# HTTPS_ENABLED=0 — sertifikat hali olinmagan sinov bosqichi uchun.
# Busiz DEBUG=0 rejimida sayt http so'rovni https ga yo'naltiradi va
# cookie'lar (Secure bayrog'i tufayli) umuman yuborilmaydi — admin panelga
# kira olmaysiz. Sertifikat olingach 1 ga qaytaring.
HTTPS_ENABLED = env_bool("HTTPS_ENABLED", True)

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = HTTPS_ENABLED
    SECURE_HSTS_SECONDS = (int(env("SECURE_HSTS_SECONDS", 31536000))
                           if HTTPS_ENABLED else 0)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = HTTPS_ENABLED
    SECURE_HSTS_PRELOAD = HTTPS_ENABLED
    SESSION_COOKIE_SECURE = HTTPS_ENABLED
    CSRF_COOKIE_SECURE = HTTPS_ENABLED
    SESSION_COOKIE_SAMESITE = "Lax"
    SECURE_CONTENT_TYPE_NOSNIFF = True

if DEV_LOGIN:
    import warnings

    warnings.warn(
        "DEV_LOGIN yoqilgan — saytga hech qanday tekshiruvsiz kirish mumkin. "
        "Ishlab chiqarishda .env da DEV_LOGIN=0 qiling!",
        RuntimeWarning, stacklevel=1,
    )

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
