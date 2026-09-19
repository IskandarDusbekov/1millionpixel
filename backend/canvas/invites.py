"""Do'st chaqirish bonusini hisoblash.

NEGA IP BO'YICHA TEKSHIRMAYMIZ. Birinchi yondashuv "chaqiruvchi va
chaqirilgan bir xil IP dan bo'lsa, bonus berilmasin" edi. O'zbekistonda
(va umuman mobil tarmoqlarda) bu ishlamaydi: CGNAT tufayli bitta operator
IP si ostida minglab abonent turadi, ya'ni bir-birini tanimaydigan haqiqiy
foydalanuvchilar ham bloklanib qolardi. Bir uyda yashovchi ikki kishi ham
shu sababdan bonus ololmasdi.

Shuning uchun himoya boshqacha:
  1. Hisob ochish uchun Telegram yoki Google kerak (allaqachon majburiy).
  2. Bonus chaqirilgan odam HAQIQATAN chiza boshlagandan keyin beriladi
     (INVITE_MIN_PIXELS piksel). Soxta hisob ochib, har biriga 20 piksel
     chizib chiqish +5 energiya uchun arzimaydigan mashaqqat.
  3. O'zini o'zi chaqirish taqiqlangan.

Piksel soni Redis'dan o'qiladi (`mp:e:<uid>` hash'idagi `n` maydoni) —
shunda bonus `drain_history` worker ishlashiga bog'liq bo'lmaydi.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.db.models import F

from .models import Player
from .redis_store import store

log = logging.getLogger(__name__)


@transaction.atomic
def credit_pending(invitee_ids: list[int] | None = None) -> int:
    """Shartni bajargan chaqiruvlar uchun bonus beradi.

    `invitee_ids` berilsa — faqat shu foydalanuvchilar tekshiriladi
    (drain_history uni hozirgina chizganlar ro'yxati bilan chaqiradi).
    """
    qs = Player.objects.filter(invite_credited=False, invited_by__isnull=False)
    if invitee_ids is not None:
        qs = qs.filter(id__in=invitee_ids)

    candidates = list(qs.values_list("id", "invited_by_id", "pixels_placed")[:500])
    if not candidates:
        return 0

    # Piksel sonini Redis'dan olamiz — shunda bonus drain_history worker
    # ishlashiga bog'liq bo'lmaydi. PostgreSQL'dagi son ham hisobga olinadi
    # (Redis kaliti 7 kundan keyin eskiradi).
    counts = store.placed_by_sync([c[0] for c in candidates])
    need = settings.INVITE_MIN_PIXELS
    rows = [(pid, inviter) for pid, inviter, db_n in candidates
            if max(counts.get(pid, 0), db_n) >= need]
    if not rows:
        return 0

    Player.objects.filter(id__in=[r[0] for r in rows]).update(
        invite_credited=True)

    per_inviter: dict[int, int] = {}
    for _, inviter_id in rows:
        per_inviter[inviter_id] = per_inviter.get(inviter_id, 0) + 1

    for inviter_id, n in per_inviter.items():
        Player.objects.filter(id=inviter_id).update(
            invites_count=F("invites_count") + n)

    # Redis'dagi shaxsiy chegarani yangilaymiz — WebSocket shundan o'qiydi
    for inviter in Player.objects.filter(id__in=per_inviter):
        store.set_max_energy_sync(inviter.id, inviter.max_energy)

    log.info("chaqiruv bonusi: %d ta foydalanuvchi, %d ta chaqiruvchi",
             len(rows), len(per_inviter))
    return len(rows)
