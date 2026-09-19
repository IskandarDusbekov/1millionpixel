"""PostgreSQL — faqat TARIX va foydalanuvchilar.

Joriy kanvas holati bu yerda emas, Redis'da. PixelEvent jadvali sekundiga
minglab satr qabul qiladi, shuning uchun u faqat yoziladi va moderatsiya
paytida o'qiladi (rollback, kim qo'ygan).
"""
import secrets

from django.conf import settings
from django.db import models


def new_code(n: int = 8) -> str:
    """Havolalar uchun qisqa kod. Adashtiradigan belgilar (0/O, 1/I) yo'q."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


class Team(models.Model):
    """Do'stlar guruhi — birga chizish uchun."""

    name = models.CharField(max_length=32, unique=True)
    code = models.CharField(max_length=12, unique=True, db_index=True,
                            default=new_code)
    owner = models.ForeignKey("Player", on_delete=models.SET_NULL, null=True,
                              related_name="owned_teams")
    pixels_placed = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-pixels_placed",)

    def __str__(self):
        return self.name


class Player(models.Model):
    """Telegram yoki Google orqali kirgan foydalanuvchi."""

    telegram_id = models.BigIntegerField(unique=True, null=True, blank=True)
    google_sub = models.CharField(max_length=64, unique=True, null=True, blank=True)

    # --- do'st chaqirish ---
    invite_code = models.CharField(max_length=12, unique=True, default=new_code,
                                   db_index=True)
    invited_by = models.ForeignKey("self", on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="invited")
    invites_count = models.IntegerField(default=0)
    # Bonus darhol berilmaydi: chaqirilgan odam haqiqatan chizishni
    # boshlagandagina hisoblanadi (INVITE_MIN_PIXELS piksel).
    invite_credited = models.BooleanField(default=False)
    team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True,
                             blank=True, related_name="members")

    username = models.CharField(max_length=64, blank=True)
    display_name = models.CharField(max_length=128, blank=True)
    photo_url = models.URLField(blank=True)

    pixels_placed = models.BigIntegerField(default=0)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    last_seen = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    is_banned = models.BooleanField(default=False)
    ban_until = models.DateTimeField(null=True, blank=True)
    ban_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["last_ip"]),
            models.Index(fields=["-last_seen"]),
            models.Index(fields=["team", "-pixels_placed"]),
        ]

    def __str__(self):
        return self.display_name or self.username or f"player#{self.pk}"

    @property
    def max_energy(self) -> int:
        """Do'st chaqirgan har bir foydalanuvchi zaxirani oshiradi.

        Viral o'sishning asosiy turtki mexanizmi: chaqirilgan do'st
        haqiqatan kirgandagina bonus beriladi (soxta hisoblardan himoya —
        kirish Telegram yoki Google orqali tekshiriladi).
        """
        bonus = min(self.invites_count * settings.INVITE_BONUS_ENERGY,
                    settings.INVITE_BONUS_MAX)
        return settings.MAX_ENERGY + bonus


class PixelEvent(models.Model):
    """Har bir bo'yash. Rollback shu jadvaldan tiklanadi."""

    x = models.SmallIntegerField()
    y = models.SmallIntegerField()
    color = models.SmallIntegerField()          # palitra indeksi 0..23
    player = models.ForeignKey(
        Player, on_delete=models.SET_NULL, null=True, db_index=False
    )
    created_at = models.DateTimeField(db_index=True)

    class Meta:
        indexes = [
            # rollback: hududdagi har bir katakning T vaqtidan oldingi
            # oxirgi holatini DISTINCT ON bilan olish uchun
            models.Index(fields=["x", "y", "-created_at"], name="px_xy_time_idx"),
            # "shu foydalanuvchi bugun nima qildi" — ban tekshiruvi
            models.Index(fields=["player", "-created_at"], name="px_player_idx"),
        ]

    def __str__(self):
        return f"({self.x},{self.y}) -> {self.color}"


class Report(models.Model):
    """Foydalanuvchi shikoyati — koordinata bilan."""

    NEW, DONE, REJECTED = "new", "done", "rejected"
    STATUS = [(NEW, "Yangi"), (DONE, "Ko'rildi"), (REJECTED, "Rad etildi")]

    x = models.SmallIntegerField()
    y = models.SmallIntegerField()
    reporter = models.ForeignKey(Player, on_delete=models.SET_NULL, null=True)
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default=NEW, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"Shikoyat ({self.x},{self.y}) — {self.get_status_display()}"


class Snapshot(models.Model):
    """Doskaning ma'lum vaqtdagi surati — timelapse video uchun.

    Fayl PNG (palitrali, ~10-60 KB). Ketma-ket suratlardan ffmpeg bilan
    tezlashtirilgan video yig'iladi.
    """

    AUTO, MILESTONE, MANUAL, FULL = "auto", "milestone", "manual", "full"
    REASONS = [(AUTO, "Jadval bo'yicha"), (MILESTONE, "Bosqich"),
               (MANUAL, "Qo'lda"), (FULL, "Doska to'ldi")]

    file = models.CharField(max_length=255)          # MEDIA_ROOT ga nisbatan
    reason = models.CharField(max_length=12, choices=REASONS, default=AUTO)
    painted = models.IntegerField(default=0)         # bo'yalgan piksellar
    fill_percent = models.FloatField(default=0)
    changed = models.IntegerField(default=0)         # oldingi suratdan beri
    online = models.IntegerField(default=0)
    bytes = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} — {self.fill_percent:.1f}%"


class ModerationLog(models.Model):
    """Admin nima qilgani — kimni ban qilgan, qaysi hududni qaytargan."""

    admin = models.CharField(max_length=64)
    action = models.CharField(max_length=32)     # rollback | ban | unban
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.admin} {self.action}"
