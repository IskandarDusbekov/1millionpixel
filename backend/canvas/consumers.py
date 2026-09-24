"""WebSocket consumer — issiq yo'l (hot path).

Bu yerda PostgreSQL'ga bironta ham so'rov yo'q. Har bir bo'yash =
bitta Redis Lua chaqiruvi. Tarix Redis Stream'ga tushadi va uni alohida
worker (manage.py drain_history) fonda PostgreSQL'ga ko'chiradi.
"""
from __future__ import annotations

import logging
import struct
import time
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from . import broadcaster as bc
from .auth import AuthError, client_ip, decode_jwt
from .redis_store import store

log = logging.getLogger(__name__)

OP_PLACE = 0x01
OP_PING = 0x02
OP_REPORT = 0x03

N = settings.CANVAS_SIZE

# Token bucket: normal odam sekundiga 20 tadan ko'p piksel qo'ya olmaydi.
RATE_REFILL = 30.0       # token/sek (mijoz chiziq tortganda ~20/s yuboradi)
RATE_BURST = 90.0

CLOSE_AUTH = 4001
CLOSE_BANNED = 4003
CLOSE_FLOOD = 4029


class PixelConsumer(AsyncWebsocketConsumer):

    # ------------------------------------------------------------ ulanish
    async def connect(self):
        qs = parse_qs((self.scope.get("query_string") or b"").decode())
        token = (qs.get("token") or [""])[0]
        try:
            self.uid = decode_jwt(token)
        except AuthError:
            await self.close(code=CLOSE_AUTH)
            return

        self.ip = client_ip(self.scope)
        if await store.is_banned(self.uid, self.ip):
            await self.close(code=CLOSE_BANNED)
            return

        self.tokens = RATE_BURST
        self.last_refill = time.monotonic()
        # Shaxsiy zaxira chegarasi (do'st chaqirish bonusi bilan).
        # Kirishda Redis'ga yozilgan — bu yerda DB ga bormaymiz.
        self.max_energy = await store.max_energy(self.uid)

        await self.accept()
        bc.clients.add(self)

        now = int(time.time() * 1000)
        await store.heartbeat(self.uid, now)

        # Boshlang'ich holat: onlayn + shaxsiy energiya
        n, cd = bc.online_now(), bc.cooldown_now()
        await self.send(bytes_data=bc.pack_online(n, cd, bc.flags_now()))
        energy, nxt = await store.energy(self.uid, now, cd, self.max_energy)
        await self.send(bytes_data=bc.pack_energy(energy, self.max_energy, nxt))

    async def disconnect(self, code):
        bc.clients.discard(self)
        uid = getattr(self, "uid", None)
        if uid is not None:
            await store.leave(uid)

    # ------------------------------------------------------------ qabul
    async def receive(self, text_data=None, bytes_data=None):
        if not bytes_data:
            return
        if not self._allow():
            await self.close(code=CLOSE_FLOOD)
            return

        op = bytes_data[0]
        if op == OP_PLACE and len(bytes_data) == 6:
            x, y, color = struct.unpack("!HHB", bytes_data[1:6])
            await self._place(x, y, color)
        elif op == OP_PING:
            # Onlayn hisobi bunga bog'liq EMAS (broadcaster.refresh_presence
            # server tomonda yangilaydi). Bu WebSocket keepalive — proksi jim
            # turgan ulanishni uzib yubormasligi uchun.
            # Ayni paytda zaxira chegarasini ham yangilaymiz: do'sti chizishni
            # boshlasa, bonus qayta ulanmasdan ham yetib boradi (~20 soniyada).
            new_max = await store.max_energy(self.uid)
            if new_max != self.max_energy:
                self.max_energy = new_max
                now = int(time.time() * 1000)
                energy, nxt = await store.energy(
                    self.uid, now, bc.cooldown_now(), new_max)
                await self.send(bytes_data=bc.pack_energy(energy, new_max, nxt))
                await self.send(bytes_data=bc.pack_toast(
                    "Do'stingiz qo'shildi — bo'yoq zaxirangiz oshdi!"))
        elif op == OP_REPORT and len(bytes_data) == 5:
            x, y = struct.unpack("!HH", bytes_data[1:5])
            await self._report(x, y)

    def _allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(RATE_BURST,
                          self.tokens + (now - self.last_refill) * RATE_REFILL)
        self.last_refill = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True

    # ------------------------------------------------------------ bo'yash
    async def _place(self, x: int, y: int, color: int):
        if not (0 <= x < N and 0 <= y < N and 0 <= color < settings.PALETTE_LEN):
            return

        if bc.readonly_now():
            # Mijoz energiyani optimistik sarflagan — haqiqiy holatni qaytaramiz
            energy, nxt = await store.energy(
                self.uid, int(time.time() * 1000), bc.cooldown_now(),
                self.max_energy)
            await self.send(bytes_data=bc.pack_energy(
                energy, self.max_energy, nxt))
            await self.send(bytes_data=bc.pack_reject(x, y))
            await self.send(bytes_data=bc.pack_toast(
                "Doska hozir faqat ko'rish rejimida"))
            return

        now = int(time.time() * 1000)
        cd = bc.cooldown_now()

        ok, energy, nxt = await store.place(self.uid, x, y, color, now, cd,
                                            self.max_energy,
                                            unlimited=bc.unlimited_now())

        # Har doim shaxsiy energiya javobi — brauzer sanagichi server bilan
        # sinxron bo'lib qoladi (foydalanuvchi soatini o'zgartirsa ham).
        await self.send(bytes_data=bc.pack_energy(energy, self.max_energy, nxt))
        if not ok:
            # Mijoz pikselni optimistik chizib qo'ygan — aniq qaysi katakni
            # qaytarish kerakligini aytamiz.
            await self.send(bytes_data=bc.pack_reject(x, y))
            await self.send(bytes_data=bc.pack_toast(
                "Bo'yoq tiklanmoqda — biroz ko'z dam oling"))
            return

        await store.heartbeat(self.uid, now)
        # Piksel mp:buf ga tushdi — broadcaster uni 80 ms ichida tarqatadi.

    # ------------------------------------------------------------ shikoyat
    async def _report(self, x: int, y: int):
        if not (0 <= x < N and 0 <= y < N):
            return
        from asgiref.sync import sync_to_async

        from .models import Report

        await sync_to_async(Report.objects.create, thread_sensitive=False)(
            x=x, y=y, reporter_id=self.uid
        )
        await self.send(bytes_data=bc.pack_toast(
            f"Shikoyat yuborildi: {x}, {y}"))
