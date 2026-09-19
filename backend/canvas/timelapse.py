"""Doska suratlari — tezlashtirilgan video (timelapse) uchun.

Nima uchun oraliq o'zgaruvchan: agar har 30 soniyada surat olsak, kechasi
hech kim chizmaganda minglab bir xil kadr yig'iladi; agar har 30 daqiqada
olsak, eng qizg'in paytdagi harakat yo'qoladi. Shuning uchun oraliq
FAOLLIKKA qarab hisoblanadi — qancha ko'p piksel qo'yilgan bo'lsa,
shuncha tez surat olinadi.

Fayl formati — palitrali PNG (rejim "P"). 1000x1000 doska odatda
10-60 KB chiqadi, chunki atigi 24 ta rang bor.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import Snapshot
from .redis_store import CANVAS_BYTES, K_PLACED, K_TIMELAPSE_ON, N, store

log = logging.getLogger(__name__)

# Frontend bilan bir xil bo'lishi SHART — aks holda suratdagi ranglar
# saytdagidan boshqacha chiqadi.
PALETTE = [
    "#FFFFFF", "#E4E4E4", "#888888", "#444444", "#111111", "#FFA7D1",
    "#E50000", "#E59500", "#A06A42", "#E5D900", "#94E044", "#02BE01",
    "#00D3DD", "#0083C7", "#0000EA", "#CF6EE4", "#820080", "#FF7F7F",
    "#FFB470", "#FFFF80", "#7FFF8E", "#7FE5F0", "#7F9EFF", "#C77FFF",
]

# Doska to'lish bosqichlari — bu nuqtalarda albatta surat olinadi
MILESTONES = (1, 5, 10, 25, 50, 75, 90, 95, 99, 100)


def _png_palette() -> list[int]:
    """PNG uchun 256 x 3 baytlik palitra (qolgani qora)."""
    flat: list[int] = []
    for hexa in PALETTE:
        flat += [int(hexa[1:3], 16), int(hexa[3:5], 16), int(hexa[5:7], 16)]
    return flat + [0, 0, 0] * (256 - len(PALETTE))


def is_enabled() -> bool:
    """Admin panelidan to'xtatilmaganmi. Sukut bo'yicha — yoqilgan."""
    v = store.sync.get(K_TIMELAPSE_ON)
    return True if v is None else v == b"1"


def set_enabled(on: bool) -> None:
    store.sync.set(K_TIMELAPSE_ON, b"1" if on else b"0")


def placed_total() -> int:
    v = store.sync.get(K_PLACED)
    return int(v) if v else 0


def next_interval(changed: int) -> int:
    """Faollikka qarab keyingi surat oralig'i (soniya).

    changed = 0                     -> eng sekin (MAX)
    changed >= TIMELAPSE_BUSY_PIXELS -> eng tez  (MIN)
    oraliqda — chiziqli.
    """
    lo, hi = settings.TIMELAPSE_MIN_SEC, settings.TIMELAPSE_MAX_SEC
    busy = max(1, settings.TIMELAPSE_BUSY_PIXELS)
    if changed <= 0:
        return hi
    if changed >= busy:
        return lo
    return int(hi - (hi - lo) * (changed / busy))


def canvas_stats(data: bytes) -> tuple[int, float]:
    """(bo'yalgan piksel, foiz). 0 bayt = bo'yalmagan."""
    painted = CANVAS_BYTES - data.count(0)
    return painted, painted / CANVAS_BYTES * 100


def crossed_milestone(before: float, after: float) -> int | None:
    for m in MILESTONES:
        if before < m <= after:
            return m
    return None


def capture(reason: str = Snapshot.AUTO, changed: int = 0) -> Snapshot:
    """Doskani PNG qilib saqlaydi va Snapshot yozuvini yaratadi."""
    from PIL import Image

    data = store.snapshot()
    painted, percent = canvas_stats(data)

    img = Image.frombytes("P", (N, N), data)
    img.putpalette(_png_palette())

    now = timezone.now()
    settings.TIMELAPSE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{now:%Y%m%d-%H%M%S}-{reason}.png"
    path = settings.TIMELAPSE_DIR / name
    img.save(path, format="PNG", optimize=True)

    snap = Snapshot.objects.create(
        file=f"timelapse/{name}", reason=reason,
        painted=painted, fill_percent=percent, changed=changed,
        online=store.online_cached_sync(), bytes=path.stat().st_size,
    )
    log.info("timelapse: %s (%.2f%%, %d o'zgarish, %d bayt)",
             name, percent, changed, snap.bytes)
    return snap


def cleanup_old() -> int:
    """TIMELAPSE_KEEP_DAYS dan eski suratlarni o'chiradi (0 = o'chirmaslik)."""
    days = settings.TIMELAPSE_KEEP_DAYS
    if days <= 0:
        return 0
    cutoff = timezone.now() - timedelta(days=days)
    old = list(Snapshot.objects.filter(created_at__lt=cutoff))
    for s in old:
        try:
            (settings.MEDIA_ROOT / s.file).unlink(missing_ok=True)
        except OSError:
            log.warning("o'chirib bo'lmadi: %s", s.file)
    Snapshot.objects.filter(created_at__lt=cutoff).delete()
    return len(old)
