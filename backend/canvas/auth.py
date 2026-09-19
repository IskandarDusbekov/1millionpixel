"""Avtorizatsiya: Telegram WebApp initData va JWT.

Yagona kirish yo'li — Telegram Mini App. Google OAuth ham, parolsiz
"dasturchi kirishi" ham ataylab olib tashlangan: imzo bot tokeni bilan
tekshirilgani uchun har bir piksel haqiqiy Telegram hisobiga bog'lanadi
va soxta hisob ochish qimmatga tushadi.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

import jwt
from django.conf import settings

TELEGRAM_MAX_AGE = 24 * 3600      # initData 24 soatdan eski bo'lmasin


class AuthError(Exception):
    pass


# --------------------------------------------------------------------------
# Telegram Mini App
# --------------------------------------------------------------------------
def verify_telegram(init_data: str) -> dict:
    """Telegram WebApp initData imzosini tekshiradi.

    Telegram hujjatiga ko'ra:
      secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
      hash       = HMAC_SHA256(key=secret_key, msg=data_check_string)
    data_check_string — "hash"dan boshqa barcha maydonlar,
    kalit bo'yicha saralangan va "\\n" bilan birlashtirilgan.
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        raise AuthError("TELEGRAM_BOT_TOKEN sozlanmagan")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", None)
    if not received:
        raise AuthError("hash yo'q")

    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData",
                      settings.TELEGRAM_BOT_TOKEN.encode(),
                      hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, received):
        raise AuthError("Imzo noto'g'ri")

    auth_date = int(pairs.get("auth_date", 0))
    if time.time() - auth_date > TELEGRAM_MAX_AGE:
        raise AuthError("initData eskirgan")

    user = json.loads(pairs.get("user", "{}"))
    if not user.get("id"):
        raise AuthError("user yo'q")

    return {
        "provider": "telegram",
        "telegram_id": int(user["id"]),
        "username": user.get("username", "") or "",
        "display_name": " ".join(
            p for p in (user.get("first_name"), user.get("last_name")) if p
        ),
        "photo_url": user.get("photo_url", "") or "",
    }


# --------------------------------------------------------------------------
# JWT — WebSocket ulanishi shu token bilan ochiladi
# --------------------------------------------------------------------------
def issue_jwt(player_id: int) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": str(player_id), "iat": now,
         "exp": now + settings.JWT_TTL_DAYS * 86400},
        settings.JWT_SECRET,
        algorithm="HS256",
    )


def decode_jwt(token: str) -> int:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        return int(payload["sub"])
    except Exception as exc:
        raise AuthError("Token yaroqsiz") from exc


def client_ip(scope_or_request) -> str:
    """Nginx/Cloudflare orqasidagi haqiqiy IP."""
    headers = getattr(scope_or_request, "META", None)
    if headers is None:                      # ASGI scope
        raw = dict(scope_or_request.get("headers") or [])
        fwd = raw.get(b"x-forwarded-for", b"").decode()
        if fwd:
            return fwd.split(",")[0].strip()
        client = scope_or_request.get("client")
        return client[0] if client else "0.0.0.0"
    fwd = headers.get(settings.REAL_IP_HEADER, "")
    if fwd:
        return fwd.split(",")[0].strip()
    return headers.get("REMOTE_ADDR", "0.0.0.0")
