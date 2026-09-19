"""Moderatsiya: hududni orqaga qaytarish (rollback) va ban.

Rollback mantig'i: hududdagi HAR BIR katak uchun T vaqtidan oldingi
OXIRGI hodisani topamiz. PostgreSQL'da buni DISTINCT ON bitta so'rovda
qiladi — katak bo'yicha tsikl aylanish shart emas.
"""
from __future__ import annotations

import struct
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from .models import ModerationLog, PixelEvent, Player
from .redis_store import K_PIXEL_CH, store

N = settings.CANVAS_SIZE
MSG_PIXELS = 0x01
MSG_RELOAD = 0x05

# Shu maydondan katta hududda alohida paket yubormaymiz — mijozlarga
# "snapshot'ni qayta yukla" deymiz (aks holda bufer portlaydi).
PACKET_LIMIT = 20_000

# PostgreSQL: DISTINCT ON eng tez yo'l — px_xy_time_idx indeksi bilan
# har bir (x, y) uchun indeksdan bitta satr o'qiladi.
ROLLBACK_SQL_PG = """
SELECT DISTINCT ON (x, y) x, y, color
FROM canvas_pixelevent
WHERE x BETWEEN %s AND %s
  AND y BETWEEN %s AND %s
  AND created_at <= %s
ORDER BY x, y, created_at DESC, id DESC
"""

# SQLite (lokal sinov) DISTINCT ON ni qo'llamaydi — oyna funksiyasi bilan.
ROLLBACK_SQL_WINDOW = """
SELECT x, y, color FROM (
  SELECT x, y, color,
         ROW_NUMBER() OVER (PARTITION BY x, y
                            ORDER BY created_at DESC, id DESC) AS rn
  FROM canvas_pixelevent
  WHERE x BETWEEN %s AND %s
    AND y BETWEEN %s AND %s
    AND created_at <= %s
) t WHERE rn = 1
"""


def _rollback_sql() -> str:
    return (ROLLBACK_SQL_PG if connection.vendor == "postgresql"
            else ROLLBACK_SQL_WINDOW)


@transaction.atomic
def rollback_area(x0: int, y0: int, x1: int, y1: int,
                  minutes: int, admin: str = "system") -> dict:
    """Hududni `minutes` daqiqa oldingi holatiga qaytaradi."""
    x0, x1 = sorted((max(0, x0), min(N - 1, x1)))
    y0, y1 = sorted((max(0, y0), min(N - 1, y1)))
    cutoff = timezone.now() - timedelta(minutes=minutes)

    with connection.cursor() as cur:
        cur.execute(_rollback_sql(), [x0, x1, y0, y1, cutoff])
        old = {(r[0], r[1]): r[2] for r in cur.fetchall()}

    width = x1 - x0 + 1
    height = y1 - y0 + 1
    area = width * height

    # Hozirgi holat — faqat haqiqatan o'zgargan piksellarni yozamiz
    current_rows = store.read_rect(x0, y0, x1, y1)
    changed: list[tuple[int, int, int]] = []

    for j, row in enumerate(current_rows):
        y = y0 + j
        row = row.ljust(width, b"\x00")
        new_row = bytearray(row)
        for i in range(width):
            x = x0 + i
            want = old.get((x, y), 0)          # tarixda yo'q bo'lsa — oq
            if new_row[i] != want:
                new_row[i] = want
                changed.append((x, y, want))
        if new_row != row:
            store.write_row(x0, y, bytes(new_row))

    # Tarixga ham yozamiz, aks holda keyingi rollback vandalizmni qaytaradi
    now = timezone.now()
    PixelEvent.objects.bulk_create(
        [PixelEvent(x=x, y=y, color=c, player=None, created_at=now)
         for x, y, c in changed],
        batch_size=5000,
    )

    _publish(changed, area)

    ModerationLog.objects.create(
        admin=admin, action="rollback",
        detail={"rect": [x0, y0, x1, y1], "minutes": minutes,
                "changed": len(changed)},
    )
    return {"changed": len(changed), "area": area,
            "rect": [x0, y0, x1, y1], "minutes": minutes}


def _publish(changed: list[tuple[int, int, int]], area: int) -> None:
    if not changed:
        return
    if len(changed) > PACKET_LIMIT or area > PACKET_LIMIT:
        store.sync.publish(K_PIXEL_CH, bytes([MSG_RELOAD]))
        return
    payload = bytes([MSG_PIXELS]) + b"".join(
        struct.pack("!HHB", x, y, c) for x, y, c in changed
    )
    store.sync.publish(K_PIXEL_CH, payload)


# --------------------------------------------------------------------------
# Ban
# --------------------------------------------------------------------------
def ban_player(player: Player, hours: int | None, reason: str,
               admin: str = "system", by_ip: bool = True) -> None:
    """Redis'da darhol bloklaydi + PostgreSQL'da qayd qiladi.

    Redis birinchi: ochiq WebSocket ulanishlar keyingi piksel urinishida
    darhol rad javobini oladi, DB ga bog'lanmasdan.
    """
    seconds = hours * 3600 if hours else None
    store.ban_sync(player.id, player.last_ip if by_ip else None, seconds)

    player.is_banned = True
    player.ban_reason = reason
    player.ban_until = (timezone.now() + timedelta(hours=hours)) if hours else None
    player.save(update_fields=["is_banned", "ban_reason", "ban_until"])

    ModerationLog.objects.create(
        admin=admin, action="ban",
        detail={"player": player.id, "ip": player.last_ip if by_ip else None,
                "hours": hours, "reason": reason},
    )


def unban_player(player: Player, admin: str = "system") -> None:
    store.unban_sync(player.id, player.last_ip)
    player.is_banned = False
    player.ban_until = None
    player.save(update_fields=["is_banned", "ban_until"])
    ModerationLog.objects.create(
        admin=admin, action="unban", detail={"player": player.id}
    )
