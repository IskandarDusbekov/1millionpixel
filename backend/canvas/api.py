"""HTTP API (Django Ninja). WebSocket'dan tashqari hamma narsa shu yerda."""
from __future__ import annotations

import gzip
import re
import secrets
import threading
import time

from django.conf import settings
from django.db.models import Count, F
from django.http import HttpResponse
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError
from ninja.security import HttpBearer

from .auth import AuthError, client_ip, decode_jwt, issue_jwt, verify_telegram
from .cooldown import cooldown_seconds
from .invites import credit_pending
from .models import Player, Report, Team
from .redis_store import store

api = NinjaAPI(title="Million Piksel", version="1.0", urls_namespace="mp",
               docs_url=None)      # /api/docs ni ochiq qoldirmaymiz

BOT_LOGIN_TTL = 300               # kod 5 daqiqa amal qiladi


class Bearer(HttpBearer):
    def authenticate(self, request, token):
        try:
            pid = decode_jwt(token)
        except AuthError:
            return None
        player = Player.objects.filter(id=pid).first()
        if not player or player.is_banned:
            return None
        request.player = player
        return player


auth = Bearer()


# --------------------------------------------------------------------------
# Sxemalar
# --------------------------------------------------------------------------
class TelegramIn(Schema):
    init_data: str
    ref: str = ""


class TokenOut(Schema):
    token: str
    player_id: int
    display_name: str
    photo_url: str


class StateOut(Schema):
    size: int
    max_energy: int
    online: int
    cooldown_sec: int
    palette_len: int
    energy: int = settings.MAX_ENERGY
    next_ms: int = 0


class ReportIn(Schema):
    x: int
    y: int
    note: str = ""


# --------------------------------------------------------------------------
# Avtorizatsiya
# --------------------------------------------------------------------------
def _apply_invite(player: Player, ref: str) -> None:
    """Chaqiruv kodini bog'laydi — faqat yangi hisob uchun, bir marta.

    Bonus BU YERDA berilmaydi: chaqirilgan odam haqiqatan chiza boshlagach,
    `invites.credit_pending()` uni hisoblaydi (izohlar o'sha faylda).
    """
    if not ref or player.invited_by_id or player.pixels_placed:
        return
    inviter = Player.objects.filter(invite_code=ref.strip().upper()).first()
    if not inviter or inviter.id == player.id:
        return

    player.invited_by = inviter
    if inviter.team_id and inviter.team.members.count() < settings.TEAM_MAX_MEMBERS:
        player.team_id = inviter.team_id      # do'st bilan bitta jamoada
    player.save(update_fields=["invited_by", "team"])


def _login(request, info: dict, ref: str = "") -> TokenOut:
    player, created = Player.objects.get_or_create(
        telegram_id=info["telegram_id"], defaults={
            "username": info["username"],
            "display_name": info["display_name"],
            "photo_url": info["photo_url"],
        })
    ip = client_ip(request)
    player.last_ip = ip
    player.display_name = info["display_name"] or player.display_name
    player.save(update_fields=["last_ip", "display_name", "last_seen"])

    if player.is_banned:
        raise HttpError(403, "Hisobingiz bloklangan")

    if created or not player.invited_by_id:
        _apply_invite(player, ref)

    # Kirish paytida kutayotgan bonuslarni hisoblab qo'yamiz — drain_history
    # ishlamayotgan bo'lsa ham foydalanuvchi bonusini ko'radi
    credit_pending([player.id])
    player.refresh_from_db(fields=["invites_count"])

    # WebSocket ulanganda DB ga bormaslik uchun chegarani Redis'ga yozamiz
    store.set_max_energy_sync(player.id, player.max_energy)

    return TokenOut(token=issue_jwt(player.id), player_id=player.id,
                    display_name=player.display_name,
                    photo_url=player.photo_url)


@api.post("/auth/telegram", response=TokenOut, auth=None)
def auth_telegram(request, data: TelegramIn):
    try:
        info = verify_telegram(data.init_data)
    except AuthError as exc:
        raise HttpError(401, str(exc)) from exc
    # Telegram start_param = "?startapp=KOD" dan keladi
    ref = data.ref or _tg_start_param(data.init_data)
    return _login(request, info, ref)


def _tg_start_param(init_data: str) -> str:
    from urllib.parse import parse_qsl
    return dict(parse_qsl(init_data)).get("start_param", "")


# Google OAuth va parolsiz "dasturchi kirishi" ataylab olib tashlangan.
# Kirish yo'llari faqat ikkita, ikkalasida ham Telegram hisobi tekshiriladi:
#   1) Mini App ichida — initData imzosi bot tokeni bilan;
#   2) Oddiy brauzerda — bot orqali (quyida): brauzer kod oladi, foydalanuvchi
#      uni botda TASDIQLAYDI, brauzer natijani so'rab turadi.


_CODE_RE = re.compile(r"^[0-9a-f]{16}$")


class BotStartOut(Schema):
    code: str
    link: str
    expires: int


def _ua_hint(request) -> str:
    """Tasdiqlash xabarida ko'rsatiladigan qisqa qurilma tavsifi."""
    ua = request.headers.get("User-Agent", "")
    browser = next((n for k, n in (("Edg/", "Edge"), ("OPR/", "Opera"),
                                   ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                                   ("Safari/", "Safari")) if k in ua), "Brauzer")
    system = next((n for k, n in (("Windows", "Windows"), ("Android", "Android"),
                                  ("iPhone", "iPhone"), ("iPad", "iPad"),
                                  ("Mac OS", "macOS"), ("Linux", "Linux"))
                   if k in ua), "")
    return f"{browser} · {system}".strip(" ·")


@api.post("/auth/bot/start", response=BotStartOut, auth=None)
def auth_bot_start(request):
    if not (settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_USERNAME):
        raise HttpError(503, "Telegram bot sozlanmagan")
    ip = client_ip(request)
    if store.rate_hit_sync(f"mp:rl:botstart:{ip}", 60) > 10:
        raise HttpError(429, "Juda ko'p urinish, bir daqiqa kuting")

    code = secrets.token_hex(8)
    store.botlogin_create_sync(code, {"ip": ip, "ua": _ua_hint(request)},
                               ttl=BOT_LOGIN_TTL)
    return BotStartOut(
        code=code, expires=BOT_LOGIN_TTL,
        link=f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start=login_{code}")


@api.get("/auth/bot/poll", auth=None)
def auth_bot_poll(request, code: str, ref: str = ""):
    if not _CODE_RE.match(code):
        raise HttpError(400, "Kod noto'g'ri")
    cur = store.botlogin_take_sync(code)
    if cur is None:
        return {"status": "expired"}
    if cur.get("s") != "ok":
        return {"status": "pending"}
    info = {"provider": "telegram", "telegram_id": int(cur["telegram_id"]),
            "username": cur.get("username", ""),
            "display_name": cur.get("display_name", ""),
            "photo_url": ""}
    out = _login(request, info, ref)
    return {"status": "ok", **out.dict()}


# --------------------------------------------------------------------------
# Ochiq sayt holati (e'lon, faqat-ko'rish rejimi) — mijoz vaqti-vaqti bilan so'raydi
# --------------------------------------------------------------------------
@api.get("/site", auth=None)
def site_info(request):
    from .site import get_settings

    s = get_settings()
    return {"name": s.site_name,
            "announcement": s.announcement if s.announcement_on else "",
            "readonly": s.readonly}


# --------------------------------------------------------------------------
# Do'st chaqirish va jamoalar
# --------------------------------------------------------------------------
class TeamIn(Schema):
    name: str = ""
    code: str = ""


@api.get("/me", auth=auth)
def me(request):
    p: Player = request.player
    # Chaqirganlarim orasida shartni bajarganlari bo'lsa — shu yerda hisoblanadi
    if credit_pending(list(p.invited.values_list("id", flat=True))):
        p.refresh_from_db(fields=["invites_count"])
    team = p.team
    return {
        "player_id": p.id,
        "display_name": p.display_name,
        "invite_code": p.invite_code,
        "invites": p.invites_count,
        "max_energy": p.max_energy,
        "bonus": p.max_energy - settings.MAX_ENERGY,
        "bonus_per_invite": settings.INVITE_BONUS_ENERGY,
        "bonus_left": settings.INVITE_BONUS_MAX - (p.max_energy - settings.MAX_ENERGY),
        "pixels": p.pixels_placed,
        "team": None if not team else {
            "name": team.name, "code": team.code,
            "members": team.members.count(),
            "pixels": team.pixels_placed,
            "is_owner": team.owner_id == p.id,
        },
    }


@api.post("/team/create", auth=auth)
def team_create(request, data: TeamIn):
    p: Player = request.player
    name = (data.name or "").strip()[:32]
    if len(name) < 3:
        raise HttpError(400, "Nom kamida 3 ta belgi bo'lsin")
    if p.team_id:
        raise HttpError(400, "Siz allaqachon jamoadasiz")
    if Team.objects.filter(name__iexact=name).exists():
        raise HttpError(409, "Bunday nomli jamoa bor")

    team = Team.objects.create(name=name, owner=p)
    p.team = team
    p.save(update_fields=["team"])
    return {"ok": True, "name": team.name, "code": team.code}


@api.post("/team/join", auth=auth)
def team_join(request, data: TeamIn):
    p: Player = request.player
    team = Team.objects.filter(code=(data.code or "").strip().upper()).first()
    if not team:
        raise HttpError(404, "Jamoa topilmadi")
    if team.members.count() >= settings.TEAM_MAX_MEMBERS:
        raise HttpError(409, "Jamoa to'lgan")
    p.team = team
    p.save(update_fields=["team"])
    return {"ok": True, "name": team.name, "members": team.members.count()}


@api.post("/team/leave", auth=auth)
def team_leave(request):
    p: Player = request.player
    p.team = None
    p.save(update_fields=["team"])
    return {"ok": True}


@api.get("/team/top", auth=None)
def team_top(request):
    rows = Team.objects.annotate(n=Count("members")).order_by("-pixels_placed")[:20]
    return {"items": [{"name": t.name, "pixels": t.pixels_placed,
                       "members": t.n} for t in rows]}


# --------------------------------------------------------------------------
# Holat
# --------------------------------------------------------------------------
@api.get("/state", response=StateOut, auth=None)
def state(request):
    online = store.online_cached_sync()
    out = StateOut(
        size=settings.CANVAS_SIZE,
        max_energy=settings.MAX_ENERGY,
        online=online,
        cooldown_sec=cooldown_seconds(online),
        palette_len=settings.PALETTE_LEN,
    )
    token = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    if token:
        try:
            pid = decode_jwt(token)
            vals = store.sync.hmget(f"mp:e:{pid}", "e", "at")
            if vals[0] and vals[1]:
                cd = cooldown_seconds(online) * 1000
                now = int(time.time() * 1000)
                e, at = int(vals[0]), int(vals[1])
                e = min(settings.MAX_ENERGY, e + (now - at) // cd)
                out.energy = e
                out.next_ms = 0 if e >= settings.MAX_ENERGY else cd - ((now - at) % cd)
        except AuthError:
            pass
    return out


@api.post("/report", auth=auth)
def report(request, data: ReportIn):
    n = settings.CANVAS_SIZE
    if not (0 <= data.x < n and 0 <= data.y < n):
        raise HttpError(400, "Koordinata noto'g'ri")
    Report.objects.create(x=data.x, y=data.y, reporter=request.player,
                          note=data.note[:255])
    return {"ok": True}


# --------------------------------------------------------------------------
# Moderatsiya (rollback / ban) bu yerda ATAYLAB yo'q.
#
# django-ninja POST'larni csrf_exempt qiladi. Sessiya bilan ishlaydigan
# admin endpointi shu holatda CSRF hujumiga ochiq bo'lardi: admin boshqa
# saytdagi formani bosishi bilan hudud o'chib ketishi mumkin edi.
# Shuning uchun ikkala amal ham Django admin panelida (canvas/admin.py),
# ya'ni CSRF himoyasi ostida qoldirildi.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Snapshot — oddiy Django view (Ninja sxemasiz, toza baytlar)
# --------------------------------------------------------------------------
_snap_lock = threading.Lock()
_snap_cache = {"at": -1e9, "raw": b"", "gz": b""}


def canvas_bin(request):
    """1 000 000 bayt, gzip bilan ~30-300 KB.

    Gziplash bir sekundda BIR MARTA bajariladi va natija keshlanadi.
    Sababi: 1 MB ni siqish ~20 ms CPU oladi. Har so'rovda qilinsa,
    sekundiga 50 ta yangi foydalanuvchi butun yadroni yeb qo'yadi
    (kuchsiz serverlarda bu eng birinchi bo'g'iz bo'ladi).
    Kesh 1 soniyalik, ya'ni foydalanuvchi ko'pi bilan 1 soniya eskirgan
    doskani oladi — qolgan o'zgarishlar baribir WebSocket orqali keladi.
    """
    now = time.monotonic()
    with _snap_lock:
        if now - _snap_cache["at"] > settings.SNAPSHOT_CACHE_SEC:
            raw = store.snapshot()
            _snap_cache.update(at=now, raw=raw, gz=gzip.compress(raw, 6))
        raw, gz = _snap_cache["raw"], _snap_cache["gz"]

    if "gzip" in request.headers.get("Accept-Encoding", ""):
        resp = HttpResponse(gz, content_type="application/octet-stream")
        resp["Content-Encoding"] = "gzip"
    else:
        resp = HttpResponse(raw, content_type="application/octet-stream")

    resp["Content-Length"] = str(len(resp.content))
    resp["Vary"] = "Accept-Encoding"
    resp["Cache-Control"] = "no-cache"
    return resp
