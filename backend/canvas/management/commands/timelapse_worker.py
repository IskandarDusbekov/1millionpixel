"""Timelapse worker — doska suratlarini muntazam olib turadi.

Alohida protsess sifatida ishlaydi (drain_history kabi), chunki 1 MB ni
PNG qilish ~50-150 ms CPU oladi va bu WebSocket'ni bloklamasligi kerak.

    python manage.py timelapse_worker

Video yig'ish (suratlar yig'ilgandan keyin):
    ffmpeg -framerate 24 -pattern_type glob -i 'media/timelapse/*.png' \
           -c:v libx264 -pix_fmt yuv420p -vf scale=1000:1000:flags=neighbor \
           timelapse.mp4
"""
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from canvas import timelapse as tl
from canvas.models import Snapshot


class Command(BaseCommand):
    help = "Doska suratlarini faollikka qarab olib turadi"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true",
                            help="Bitta surat olib chiqadi")

    def handle(self, *args, **opts):
        if opts["once"]:
            snap = tl.capture(Snapshot.MANUAL)
            self.stdout.write(self.style.SUCCESS(f"saqlandi: {snap.file}"))
            return

        if getattr(settings, "FAKE_REDIS", False):
            self.stderr.write(self.style.WARNING(
                "DIQQAT: FAKE_REDIS yoqilgan. Redis protsess ICHIDA, ya'ni bu "
                "worker web-server bilan bitta doskani bo'lishmaydi va "
                "suratlar BO'SH chiqadi. Lokal sinov uchun admin paneldagi "
                "'Hozir surat ol' tugmasidan foydalaning."))

        self.stdout.write(self.style.SUCCESS("timelapse_worker ishga tushdi"))

        last = Snapshot.objects.first()          # ordering = -created_at

        # Boshlang'ich kadr. Busiz timelapse birinchi suratni MAX_SEC
        # (30 daqiqa) kutardi va boshlanishi yo'qolardi.
        need_base = last is None or (
            timezone.now() - last.created_at
        ).total_seconds() > settings.TIMELAPSE_MIN_SEC
        if need_base and tl.is_enabled():
            try:
                last = tl.capture(Snapshot.AUTO, 0)
                self.stdout.write(f"boshlang'ich kadr: {last.file}")
            except Exception as exc:
                self.stderr.write(f"boshlang'ich kadr olinmadi: {exc}")

        last_percent = last.fill_percent if last else 0.0
        last_placed = tl.placed_total()
        wait = tl.next_interval(0)
        waited = 0
        tick = 5

        while True:
            time.sleep(tick)
            waited += tick

            if not tl.is_enabled():
                waited = 0                       # to'xtatilgan — sanagich nolga
                continue

            placed = tl.placed_total()
            changed = max(0, placed - last_placed)

            # Bosqichdan o'tgan bo'lsa — kutmasdan surat olamiz
            data = tl.store.snapshot()
            _, percent = tl.canvas_stats(data)
            milestone = tl.crossed_milestone(last_percent, percent)

            if milestone is None and waited < wait:
                continue

            if milestone == 100:
                reason = Snapshot.FULL
            elif milestone is not None:
                reason = Snapshot.MILESTONE
            else:
                reason = Snapshot.AUTO

            # Jimjitlikda bir xil kadrni takrorlamaymiz
            if reason == Snapshot.AUTO and changed == 0:
                waited = 0
                wait = tl.next_interval(0)
                continue

            try:
                tl.capture(reason, changed)
            except Exception as exc:             # disk to'ldi va h.k.
                self.stderr.write(f"surat olinmadi: {exc}")

            last_placed = placed
            last_percent = percent
            waited = 0
            wait = tl.next_interval(changed)

            if milestone:
                self.stdout.write(self.style.SUCCESS(
                    f"BOSQICH: doska {milestone}% to'ldi"))

            removed = tl.cleanup_old()
            if removed:
                self.stdout.write(f"{removed} ta eski surat o'chirildi")
