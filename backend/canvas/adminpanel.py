"""Admin panel — moderatsiya, SEO, fayllar, sayt sozlamalari.

Kirish: FAQAT superuser, login va parol bilan (`/admin/panel/login/`).
Oddiy "staff" hisoblar ham kira olmaydi.

Xavfsizlik: barcha view'lar `superuser_required` ostida va oddiy Django
view'lar bo'lgani uchun CsrfViewMiddleware ularni himoya qiladi. POST'lar
`X-CSRFToken` sarlavhasini talab qiladi (panel uni o'zi yuboradi).
Shuning uchun bu endpointlar django-ninja'da emas — u POST'larni
csrf_exempt qiladi.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate, TruncHour
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from . import site as sitemod
from .auth import client_ip
from .broadcaster import cooldown_now, online_now
from .cooldown import cooldown_seconds
from .models import (ModerationLog, PixelEvent, Player, Report, SiteSettings,
                     Team, UploadedFile)
from .moderation import ban_player, rollback_area, unban_player
from .redis_store import K_HIST, store

LOGIN_PATH = "/admin/panel/login/"
LOGIN_MAX_FAILS = 8            # shuncha xatodan keyin IP vaqtincha bloklanadi
LOGIN_WINDOW_SEC = 15 * 60


# --------------------------------------------------------------------------
# Kirish: faqat superuser
# --------------------------------------------------------------------------
def superuser_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        u = request.user
        if u.is_authenticated and u.is_active and u.is_superuser:
            return view(request, *args, **kwargs)
        if request.path.startswith("/admin/panel/api/"):
            return JsonResponse({"detail": "Kirish talab qilinadi"}, status=401)
        return redirect(f"{LOGIN_PATH}?next={request.path}")
    return wrapper


def login_view(request):
    u = request.user
    if u.is_authenticated and u.is_active and u.is_superuser:
        return redirect("/admin/panel/")

    nxt = request.POST.get("next") or request.GET.get("next") or "/admin/panel/"
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = "/admin/panel/"

    error = ""
    if request.method == "POST":
        key = f"mp:rl:adminlogin:{client_ip(request)}"
        if store.rate_get_sync(key) >= LOGIN_MAX_FAILS:
            error = "Juda ko'p urinish. 15 daqiqadan keyin qayta urinib ko'ring."
        else:
            user = authenticate(request,
                                username=(request.POST.get("username") or "").strip(),
                                password=request.POST.get("password") or "")
            if user is not None and user.is_active and user.is_superuser:
                store.key_delete_sync(key)
                login(request, user)
                ModerationLog.objects.create(admin=user.username, action="login",
                                             detail={"ip": client_ip(request)})
                return redirect(nxt)
            store.rate_hit_sync(key, LOGIN_WINDOW_SEC)
            # Sababini aytmaymiz: "parol xato" / "superuser emas" farqi sizib chiqmasin
            error = "Login yoki parol noto'g'ri."

    resp = render(request, "admin/login.html", {"error": error, "next": nxt})
    resp["Cache-Control"] = "no-store"
    return resp


@require_POST
def logout_view(request):
    logout(request)
    return redirect(LOGIN_PATH)


def _body(request) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except ValueError as exc:
        raise Http404("JSON noto'g'ri") from exc


def _int(data, key, lo, hi, default=None):
    try:
        v = int(data[key])
    except (KeyError, TypeError, ValueError):
        if default is None:
            raise ValueError(f"{key} yo'q")
        return default
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------
# Sahifa
# --------------------------------------------------------------------------
@superuser_required
def panel(request):
    resp = render(request, "admin/panel.html", {"user": request.user})
    resp["Cache-Control"] = "no-store"
    return resp


# --------------------------------------------------------------------------
# Statistika
# --------------------------------------------------------------------------
@superuser_required
@require_GET
def stats(request):
    from . import timelapse as tl

    online = store.online_cached_sync()
    now = timezone.now()
    day_ago = now - timedelta(days=1)
    hour_ago = now - timedelta(hours=1)

    painted, percent = tl.canvas_stats(store.snapshot())

    return JsonResponse({
        "online": online,
        "cooldown_sec": cooldown_seconds(online),
        "reports_new": Report.objects.filter(status=Report.NEW).count(),
        "players": Player.objects.count(),
        "players_24h": Player.objects.filter(last_seen__gte=day_ago).count(),
        "new_players_24h": Player.objects.filter(created_at__gte=day_ago).count(),
        "banned": Player.objects.filter(is_banned=True).count(),
        "teams": Team.objects.count(),
        "invites": Player.objects.filter(invited_by__isnull=False).count(),
        "events_buffered": store.sync.xlen(K_HIST),
        "events_24h": PixelEvent.objects.filter(created_at__gte=day_ago).count(),
        "events_1h": PixelEvent.objects.filter(created_at__gte=hour_ago).count(),
        "painted": painted,
        "fill_percent": round(percent, 3),
        "placed_total": tl.placed_total(),
        "timelapse_on": tl.is_enabled(),
        "process_online": online_now(),
        "process_cooldown_ms": cooldown_now(),
        **sitemod.effective(sitemod.get_settings()),
    })


@superuser_required
@require_GET
def series(request):
    """Grafiklar uchun: oxirgi 24 soat (soatma-soat) va 14 kunlik yangi foydalanuvchilar."""
    now = timezone.now()
    start_h = (now - timedelta(hours=23)).replace(minute=0, second=0, microsecond=0)
    hours = {r["h"]: r["n"] for r in
             PixelEvent.objects.filter(created_at__gte=start_h)
             .annotate(h=TruncHour("created_at")).values("h")
             .annotate(n=Count("id"))}
    hourly = []
    for i in range(24):
        h = start_h + timedelta(hours=i)
        hourly.append({"t": h.isoformat(), "n": hours.get(h, 0)})

    start_d = (now - timedelta(days=13)).date()
    days = {r["d"]: r["n"] for r in
            Player.objects.filter(created_at__date__gte=start_d)
            .annotate(d=TruncDate("created_at")).values("d")
            .annotate(n=Count("id"))}
    daily = []
    for i in range(14):
        d = start_d + timedelta(days=i)
        daily.append({"t": d.isoformat(), "n": days.get(d, 0)})
    return JsonResponse({"hourly": hourly, "daily": daily})


# --------------------------------------------------------------------------
# Shikoyatlar
# --------------------------------------------------------------------------
@superuser_required
@require_GET
def reports(request):
    status = request.GET.get("status", Report.NEW)
    qs = (Report.objects.select_related("reporter")
          .filter(status=status).order_by("-created_at")[:200])
    return JsonResponse({"items": [{
        "id": r.id, "x": r.x, "y": r.y,
        "reporter": r.reporter.display_name if r.reporter else "—",
        "reporter_id": r.reporter_id,
        "note": r.note,
        "created_at": r.created_at.isoformat(),
    } for r in qs]})


@superuser_required
@require_POST
def report_status(request):
    data = _body(request)
    rid = data.get("id")
    status = data.get("status")
    if status not in (Report.NEW, Report.DONE, Report.REJECTED):
        return HttpResponseBadRequest("status noto'g'ri")
    n = Report.objects.filter(id=rid).update(status=status)
    if not n:
        raise Http404
    return JsonResponse({"ok": True})


# --------------------------------------------------------------------------
# Piksel tarixi — "bu yerga kim qo'ygan"
# --------------------------------------------------------------------------
@superuser_required
@require_GET
def pixel_history(request):
    try:
        x = _int(request.GET, "x", 0, 999)
        y = _int(request.GET, "y", 0, 999)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    qs = (PixelEvent.objects.select_related("player")
          .filter(x=x, y=y).order_by("-created_at")[:30])
    return JsonResponse({"x": x, "y": y, "items": [{
        "color": e.color,
        "player_id": e.player_id,
        "player": e.player.display_name if e.player else "— (rollback)",
        "banned": e.player.is_banned if e.player else False,
        "created_at": e.created_at.isoformat(),
    } for e in qs]})


@superuser_required
@require_GET
def region_authors(request):
    """Hududda kim ko'p bo'yagan — ommaviy vandalizmda kerak bo'ladi."""
    try:
        x0 = _int(request.GET, "x0", 0, 999)
        y0 = _int(request.GET, "y0", 0, 999)
        x1 = _int(request.GET, "x1", 0, 999)
        y1 = _int(request.GET, "y1", 0, 999)
        minutes = _int(request.GET, "minutes", 1, 1440, 60)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    since = timezone.now() - timedelta(minutes=minutes)

    rows = (PixelEvent.objects
            .filter(x__gte=x0, x__lte=x1, y__gte=y0, y__lte=y1,
                    created_at__gte=since, player__isnull=False)
            .values("player_id", "player__display_name", "player__is_banned")
            .annotate(n=Count("id")).order_by("-n")[:20])
    return JsonResponse({"items": [{
        "player_id": r["player_id"],
        "player": r["player__display_name"] or f"player#{r['player_id']}",
        "banned": r["player__is_banned"],
        "count": r["n"],
    } for r in rows]})


# --------------------------------------------------------------------------
# Rollback
# --------------------------------------------------------------------------
@superuser_required
@require_POST
def rollback(request):
    data = _body(request)
    try:
        x0 = _int(data, "x0", 0, 999)
        y0 = _int(data, "y0", 0, 999)
        x1 = _int(data, "x1", 0, 999)
        y1 = _int(data, "y1", 0, 999)
        minutes = _int(data, "minutes", 1, 1440, 15)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    res = rollback_area(x0, y0, x1, y1, minutes, admin=request.user.username)
    return JsonResponse(res)


# --------------------------------------------------------------------------
# Foydalanuvchilar / ban
# --------------------------------------------------------------------------
@superuser_required
@require_GET
def players(request):
    q = (request.GET.get("q") or "").strip()
    qs = Player.objects.all()
    if q:
        if q.isdigit():
            qs = qs.filter(id=int(q))
        else:
            qs = qs.filter(display_name__icontains=q)
    qs = qs.order_by("-last_seen")[:50]
    return JsonResponse({"items": [{
        "id": p.id, "name": str(p), "username": p.username,
        "telegram_id": p.telegram_id, "ip": p.last_ip,
        "pixels": p.pixels_placed, "banned": p.is_banned,
        "ban_until": p.ban_until.isoformat() if p.ban_until else None,
        "last_seen": p.last_seen.isoformat(),
    } for p in qs]})


@superuser_required
@require_POST
def ban(request):
    data = _body(request)
    player = Player.objects.filter(id=data.get("player_id")).first()
    if not player:
        raise Http404("Foydalanuvchi topilmadi")
    hours = data.get("hours")
    hours = int(hours) if hours else None
    ban_player(player, hours, (data.get("reason") or "")[:255],
               admin=request.user.username, by_ip=bool(data.get("by_ip", True)))
    return JsonResponse({"ok": True, "banned": True})


@superuser_required
@require_POST
def unban(request):
    data = _body(request)
    player = Player.objects.filter(id=data.get("player_id")).first()
    if not player:
        raise Http404("Foydalanuvchi topilmadi")
    unban_player(player, admin=request.user.username)
    return JsonResponse({"ok": True, "banned": False})


# --------------------------------------------------------------------------
# Timelapse — doska suratlari
# --------------------------------------------------------------------------
@superuser_required
@require_GET
def timelapse_list(request):
    from . import timelapse as tl
    from .models import Snapshot

    qs = Snapshot.objects.all()[:60]
    total = Snapshot.objects.count()
    size = Snapshot.objects.aggregate(s=Sum("bytes"))["s"] or 0
    last = qs[0] if qs else None

    return JsonResponse({
        "enabled": tl.is_enabled(),
        "total": total,
        "total_mb": round(size / 1_048_576, 1),
        "min_sec": settings.TIMELAPSE_MIN_SEC,
        "max_sec": settings.TIMELAPSE_MAX_SEC,
        "next_sec": tl.next_interval(last.changed if last else 0),
        "fill_percent": round(last.fill_percent, 2) if last else 0,
        "items": [{
            "id": s.id,
            "url": settings.MEDIA_URL + s.file,
            "reason": s.get_reason_display(),
            "reason_key": s.reason,
            "fill": round(s.fill_percent, 2),
            "changed": s.changed,
            "online": s.online,
            "kb": round(s.bytes / 1024),
            "created_at": s.created_at.isoformat(),
        } for s in qs],
    })


@superuser_required
@require_POST
def timelapse_toggle(request):
    from . import timelapse as tl

    data = _body(request)
    on = bool(data.get("enabled"))
    tl.set_enabled(on)
    ModerationLog.objects.create(
        admin=request.user.username, action="timelapse",
        detail={"enabled": on},
    )
    return JsonResponse({"ok": True, "enabled": on})


@superuser_required
@require_POST
def timelapse_shoot(request):
    """Hoziroq bitta surat olish."""
    from . import timelapse as tl
    from .models import Snapshot

    try:
        snap = tl.capture(Snapshot.MANUAL)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)
    return JsonResponse({"ok": True, "url": settings.MEDIA_URL + snap.file,
                         "fill": round(snap.fill_percent, 2)})


# --------------------------------------------------------------------------
# Jamoalar / jurnal
# --------------------------------------------------------------------------
@superuser_required
@require_GET
def teams(request):
    rows = (Team.objects.annotate(n=Count("members"))
            .order_by("-pixels_placed")[:50])
    return JsonResponse({"items": [{
        "id": t.id, "name": t.name, "code": t.code,
        "members": t.n, "pixels": t.pixels_placed,
        "owner": str(t.owner) if t.owner else "—",
        "created_at": t.created_at.isoformat(),
    } for t in rows]})


@superuser_required
@require_GET
def logs(request):
    qs = ModerationLog.objects.order_by("-created_at")[:100]
    return JsonResponse({"items": [{
        "admin": m.admin, "action": m.action, "detail": m.detail,
        "created_at": m.created_at.isoformat(),
    } for m in qs]})


# --------------------------------------------------------------------------
# Sayt sozlamalari + SEO
# --------------------------------------------------------------------------
_TEXT_FIELDS = {          # nom -> (max uzunlik)
    "site_name": 60, "seo_title": 70, "seo_description": 320,
    "seo_keywords": 255, "seo_text": 4000, "twitter": 40, "locale": 10,
    "google_verification": 120, "yandex_verification": 120,
    "announcement": 200,
}
_BOOL_FIELDS = ("robots_index", "announcement_on", "readonly", "unlimited")
_DT_FIELDS = ("draw_from", "draw_until", "unlimited_from", "unlimited_until")
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_VERIFY_RE = re.compile(r"^[A-Za-z0-9_\-]*$")
_GFILE_RE = re.compile(r"^(google[0-9a-f]{8,40}\.html)?$")


def _site_dict(s: SiteSettings) -> dict:
    d = {f: getattr(s, f) for f in (*_TEXT_FIELDS, *_BOOL_FIELDS, "public_url",
                                    "og_image", "favicon", "theme_color",
                                    "google_file", "cooldown_override")}
    for f in _DT_FIELDS:
        v = getattr(s, f)
        d[f] = v.isoformat() if v else None
    d["effective"] = sitemod.effective(s)
    d["updated_at"] = s.updated_at.isoformat()
    return d


@superuser_required
@require_GET
def site_get(request):
    return JsonResponse(_site_dict(SiteSettings.load()))


def _clean_asset(v: str) -> str:
    """Rasm manzili: bo'sh, /media/... yoki to'liq https:// manzil."""
    v = (v or "").strip()
    if v and not (v.startswith("/media/") or re.match(r"^https?://\S+$", v)):
        raise ValueError("Rasm manzili /media/... yoki https://... bo'lsin")
    return v[:255]


@superuser_required
@require_POST
def site_save(request):
    data = _body(request)
    s = SiteSettings.load()
    changed = []
    try:
        for f, mx in _TEXT_FIELDS.items():
            if f in data:
                v = str(data[f] or "").strip()
                if len(v) > mx:
                    raise ValueError(f"«{f}» juda uzun (eng ko'pi {mx} belgi)")
                if f in ("google_verification", "yandex_verification") \
                        and not _VERIFY_RE.match(v):
                    raise ValueError("Tasdiqlash kodida faqat harf, raqam, - va _ bo'lsin")
                if f in ("site_name", "seo_title") and not v:
                    raise ValueError("Nom va sarlavha bo'sh bo'lmasin")
                setattr(s, f, v)
                changed.append(f)
        for f in _BOOL_FIELDS:
            if f in data:
                setattr(s, f, bool(data[f]))
                changed.append(f)
        if "public_url" in data:
            v = str(data["public_url"] or "").strip().rstrip("/")
            if v and not re.match(r"^https?://[^\s/]+$", v):
                raise ValueError("Asosiy manzil: https://domen.uz ko'rinishida (yo'lsiz)")
            s.public_url = v
            changed.append("public_url")
        for f in ("og_image", "favicon"):
            if f in data:
                setattr(s, f, _clean_asset(data[f]))
                changed.append(f)
        if "google_file" in data:
            v = str(data["google_file"] or "").strip()
            if not _GFILE_RE.match(v):
                raise ValueError("Google fayl nomi google1a2b3c….html ko'rinishida bo'lsin")
            s.google_file = v
            changed.append("google_file")
        if "cooldown_override" in data:
            try:
                v = int(data["cooldown_override"] or 0)
            except (TypeError, ValueError):
                raise ValueError("Tiklanish vaqti butun son bo'lsin")
            if not 0 <= v <= 3600:
                raise ValueError("Tiklanish vaqti 0 dan 3600 soniyagacha")
            s.cooldown_override = v
            changed.append("cooldown_override")
        for f in _DT_FIELDS:
            if f in data:
                raw = data[f]
                dt = None
                if raw:
                    dt = parse_datetime(str(raw))
                    if dt is None:
                        raise ValueError(f"«{f}» sanasi noto'g'ri")
                    if timezone.is_naive(dt):
                        dt = timezone.make_aware(dt)
                setattr(s, f, dt)
                changed.append(f)
        for a, b, label in (("draw_from", "draw_until", "Chizish oynasi"),
                            ("unlimited_from", "unlimited_until", "Cheksiz oyna")):
            if getattr(s, a) and getattr(s, b) and getattr(s, a) >= getattr(s, b):
                raise ValueError(f"{label}: boshlanish tugashdan oldin bo'lsin")
        if "theme_color" in data:
            v = str(data["theme_color"] or "").strip()
            if not _COLOR_RE.match(v):
                raise ValueError("Rang #RRGGBB ko'rinishida bo'lsin")
            s.theme_color = v
            changed.append("theme_color")
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    s.save()
    sitemod.invalidate()
    sitemod.mirror_control(s)
    ModerationLog.objects.create(admin=request.user.username, action="site",
                                 detail={"fields": changed})
    return JsonResponse({"ok": True, **_site_dict(s)})


# --------------------------------------------------------------------------
# Fayllar
# --------------------------------------------------------------------------
MAX_UPLOAD = 5 * 1024 * 1024
MAX_SIDE = 8000
_IMAGE_EXT = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
              "gif": "image/gif", "webp": "image/webp", "ico": "image/x-icon"}
_OTHER_EXT = {"pdf": "application/pdf", "txt": "text/plain"}
# SVG ataylab yo'q: u ichida skript bo'lishi mumkin va bir domenda ochilsa XSS beradi.


def _file_dict(f: UploadedFile, s: SiteSettings) -> dict:
    url = f.file.url
    return {
        "id": f.id, "name": f.name, "url": url, "type": f.content_type,
        "size": f.size, "w": f.width, "h": f.height,
        "is_image": f.content_type.startswith("image/"),
        "is_og": s.og_image == url, "is_favicon": s.favicon == url,
        "by": f.uploaded_by, "created_at": f.created_at.isoformat(),
    }


@superuser_required
@require_GET
def files_list(request):
    s = SiteSettings.load()
    qs = UploadedFile.objects.all()[:200]
    total = UploadedFile.objects.aggregate(t=Sum("size"))["t"] or 0
    return JsonResponse({"items": [_file_dict(f, s) for f in qs],
                         "total_mb": round(total / 1_048_576, 2),
                         "max_mb": MAX_UPLOAD // 1_048_576,
                         "allowed": sorted({*_IMAGE_EXT, *_OTHER_EXT})})


@superuser_required
@require_POST
def file_upload(request):
    up = request.FILES.get("file")
    if not up:
        return JsonResponse({"detail": "Fayl tanlanmagan"}, status=400)
    if up.size > MAX_UPLOAD:
        return JsonResponse(
            {"detail": f"Fayl {MAX_UPLOAD // 1_048_576} MB dan oshmasin"}, status=400)

    name = (up.name or "fayl").replace("\\", "/").rsplit("/", 1)[-1][:200]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    ctype = _IMAGE_EXT.get(ext) or _OTHER_EXT.get(ext)
    if not ctype:
        return JsonResponse({"detail": "Bu fayl turi ruxsat etilmagan. Mumkin: "
                             + ", ".join(sorted({*_IMAGE_EXT, *_OTHER_EXT}))},
                            status=400)

    w = h = 0
    if ext in _IMAGE_EXT:
        # Kengaytmaga ishonmaymiz: haqiqatan rasm ekanini tekshiramiz
        from PIL import Image
        try:
            with Image.open(up) as im:
                im.verify()
            up.seek(0)
            with Image.open(up) as im:
                w, h = im.size
                kind = (im.format or "").lower()
        except Exception:
            return JsonResponse({"detail": "Fayl buzuq yoki rasm emas"}, status=400)
        if kind == "jpeg":
            kind = "jpg"
        if (kind != ext) and not (kind == "jpg" and ext == "jpeg") \
                and not (ext == "ico" and kind in ("ico", "png")):
            return JsonResponse({"detail": "Fayl mazmuni kengaytmasiga mos emas"},
                                status=400)
        if w > MAX_SIDE or h > MAX_SIDE:
            return JsonResponse({"detail": f"Rasm {MAX_SIDE}px dan katta bo'lmasin"},
                                status=400)
        up.seek(0)

    obj = UploadedFile(name=name, content_type=ctype, size=up.size, width=w,
                       height=h, uploaded_by=request.user.username)
    obj.file.save(name, up, save=True)
    ModerationLog.objects.create(admin=request.user.username, action="upload",
                                 detail={"name": name, "size": up.size})
    return JsonResponse({"ok": True, **_file_dict(obj, SiteSettings.load())})


@superuser_required
@require_POST
def file_delete(request):
    data = _body(request)
    obj = UploadedFile.objects.filter(id=data.get("id")).first()
    if not obj:
        raise Http404("Fayl topilmadi")
    url = obj.file.url
    s = SiteSettings.load()
    fields = []
    if s.og_image == url:
        s.og_image = ""
        fields.append("og_image")
    if s.favicon == url:
        s.favicon = ""
        fields.append("favicon")
    if fields:
        s.save(update_fields=fields + ["updated_at"])
        sitemod.invalidate()
    name = obj.name
    obj.file.delete(save=False)
    obj.delete()
    ModerationLog.objects.create(admin=request.user.username, action="file-delete",
                                 detail={"name": name})
    return JsonResponse({"ok": True})


# --------------------------------------------------------------------------
# Hisob: parolni almashtirish
# --------------------------------------------------------------------------
@superuser_required
@require_POST
def password_change(request):
    data = _body(request)
    old, new = data.get("old") or "", data.get("new") or ""
    user = request.user
    if not user.check_password(old):
        return JsonResponse({"detail": "Joriy parol noto'g'ri"}, status=400)
    if len(new) < 10:
        return JsonResponse({"detail": "Yangi parol kamida 10 belgi bo'lsin"},
                            status=400)
    if new == old:
        return JsonResponse({"detail": "Yangi parol eskisidan farq qilsin"},
                            status=400)
    user.set_password(new)
    user.save(update_fields=["password"])
    update_session_auth_hash(request, user)       # joriy sessiya uzilmasin
    ModerationLog.objects.create(admin=user.username, action="password",
                                 detail={"ip": client_ip(request)})
    return JsonResponse({"ok": True})
