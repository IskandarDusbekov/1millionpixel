"""Redis Stream -> PostgreSQL ko'chirgich.

Issiq yo'lda (WebSocket) PostgreSQL'ga yozish mumkin emas — sekundiga
minglab INSERT WebSocket'ni bloklab qo'yadi. Shuning uchun har bir bo'yash
avval Redis Stream'ga (mp:hist) tushadi, bu worker esa uni 500 tadan
to'plab bitta bulk_create bilan bazaga ko'chiradi.

Ishga tushirish (alohida protsess yoki container):
    python manage.py drain_history
"""
import time
from datetime import datetime, timezone as dt_timezone

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F

from canvas.invites import credit_pending
from canvas.models import PixelEvent, Player, Team
from canvas.redis_store import K_HIST, N, store

GROUP = "pg"
CONSUMER = "worker-1"
BATCH = 500
BLOCK_MS = 2000


class Command(BaseCommand):
    help = "Redis Stream'dagi piksel tarixini PostgreSQL'ga ko'chiradi"

    def handle(self, *args, **opts):
        r = store.sync
        try:
            r.xgroup_create(K_HIST, GROUP, id="0", mkstream=True)
        except Exception:
            pass  # guruh allaqachon bor

        self.stdout.write(self.style.SUCCESS("drain_history ishga tushdi"))
        while True:
            try:
                resp = r.xreadgroup(GROUP, CONSUMER, {K_HIST: ">"},
                                    count=BATCH, block=BLOCK_MS)
            except Exception as exc:
                self.stderr.write(f"xreadgroup: {exc}")
                time.sleep(1)
                continue

            if not resp:
                continue

            entries = resp[0][1]
            rows, ids, counts = [], [], {}
            for entry_id, fields in entries:
                try:
                    uid = int(fields[b"u"])
                    off = int(fields[b"o"])
                    color = int(fields[b"c"])
                    ts = int(fields[b"t"]) / 1000
                except (KeyError, ValueError):
                    ids.append(entry_id)
                    continue
                rows.append(PixelEvent(
                    x=off % N, y=off // N, color=color, player_id=uid,
                    created_at=datetime.fromtimestamp(ts, dt_timezone.utc),
                ))
                counts[uid] = counts.get(uid, 0) + 1
                ids.append(entry_id)

            if rows:
                with transaction.atomic():
                    PixelEvent.objects.bulk_create(rows, batch_size=BATCH)
                    for uid, n in counts.items():
                        Player.objects.filter(id=uid).update(
                            pixels_placed=F("pixels_placed") + n
                        )
                    # Jamoa hisobi — reytingda ishlatiladi
                    teams = dict(
                        Player.objects.filter(id__in=counts, team__isnull=False)
                        .values_list("id", "team_id")
                    )
                    per_team: dict[int, int] = {}
                    for uid, n in counts.items():
                        tid = teams.get(uid)
                        if tid:
                            per_team[tid] = per_team.get(tid, 0) + n
                    for tid, n in per_team.items():
                        Team.objects.filter(id=tid).update(
                            pixels_placed=F("pixels_placed") + n
                        )
                # Chizishni boshlagan chaqirilganlar uchun bonus
                credit_pending(list(counts))
            if ids:
                r.xack(K_HIST, GROUP, *ids)
