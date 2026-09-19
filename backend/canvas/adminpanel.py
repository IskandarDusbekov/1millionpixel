"""Moderatsiya paneli — jonli kanvas, rollback, ban, shikoyatlar.

Xavfsizlik: barcha view'lar `staff_member_required` ostida va oddiy Django
view'lar bo'lgani uchun CsrfViewMiddleware ularni himoya qiladi. POST'lar
`X-CSRFToken` sarlavhasini talab qiladi (panel uni o'zi yuboradi).
Shuning uchun bu endpointlar django-ninja'da emas — u POST'larni
csrf_exempt qiladi.
"""
from __future__ import annotations

import json
from datetime import timedelta

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Sum
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .broadcaster import cooldown_now, online_now
from .cooldown import cooldown_seconds
from .models import ModerationLog, PixelEvent, Player, Report, Team
from .moderation import ban_player, rollback_area, unban_player
from .redis_store import K_HIST, store


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
@staff_member_required
def panel(request):
    return render(request, "admin/panel.html", {"user": request.user})


# --------------------------------------------------------------------------
# Statistika
# --------------------------------------------------------------------------
@staff_member_required
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
    })


# --------------------------------------------------------------------------
# Shikoyatlar
# --------------------------------------------------------------------------
@staff_member_required
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


@staff_member_required
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
@staff_member_required
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


@staff_member_required
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
@staff_member_required
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
@staff_member_required
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


@staff_member_required
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


@staff_member_required
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
@staff_member_required
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


@staff_member_required
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


@staff_member_required
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
# Jamoalar
# --------------------------------------------------------------------------
@staff_member_required
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


@staff_member_required
@require_GET
def logs(request):
    qs = ModerationLog.objects.order_by("-created_at")[:100]
    return JsonResponse({"items": [{
        "admin": m.admin, "action": m.action, "detail": m.detail,
        "created_at": m.created_at.isoformat(),
    } for m in qs]})
