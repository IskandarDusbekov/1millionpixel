"""Broadcast qatlami — 1000+ foydalanuvchi uchun eng nozik joy.

MUAMMO: har bir piksel uchun alohida WebSocket xabari yuborilsa,
1000 kishi x sekundiga 20 piksel = 20 000 xabar/sek, har biri 1000 ta
socketga = 20 000 000 send/sek. Hech qanday server buni ko'tarmaydi.

YECHIM — ikki bosqichli:
  1. BATCHING. Piksellar Redis ro'yxatiga (mp:buf) yig'iladi va har 80 ms da
     bitta binar paketga qadoqlanadi. 20 000 xabar o'rniga sekundiga 12.5 ta.
  2. PROTSESS DARAJASIDA FAN-OUT. Har bir server protsessi Redis pub/sub'ga
     BITTA marta obuna bo'ladi va paketni o'zidagi socketlarga tarqatadi.
     (Channels'ning group_send'i har bir socket uchun alohida Redis yozuvi
     qiladi — shuning uchun bu yerda undan foydalanilmadi.)

Format — binar, JSON emas (~10 barobar kichik, parse qilish tekin):
  0x01 | [uint16 x][uint16 y][uint8 c] * n      piksellar
  0x02 | [uint32 online][uint32 cooldown_ms]    onlayn holati
  0x03 | [uint16 energy][uint16 max][uint32 next_ms]   shaxsiy energiya
  0x04 | utf-8 matn                             shaxsiy xabar (toast)
  0x05 | (bo'sh)                                snapshot'ni qayta yukla
  0x06 | [uint16 x][uint16 y]                   bo'yash rad etildi, qaytar
"""
from __future__ import annotations

import asyncio
import logging
import struct
import time

from django.conf import settings

from .cooldown import cooldown_ms
from .redis_store import (K_BUF, K_ONLINE, K_ONLINE_CH, K_PIXEL_CH, store)

log = logging.getLogger(__name__)

# Shu protsessdagi ochiq socketlar. Consumer o'zini qo'shadi/olib tashlaydi.
clients: set = set()

MSG_PIXELS = 0x01
MSG_ONLINE = 0x02
MSG_ENERGY = 0x03
MSG_TOAST = 0x04
MSG_RELOAD = 0x05       # snapshot'ni qayta yuklash (katta rollback'dan keyin)
MSG_REJECT = 0x06       # optimistik bo'yashni bekor qilish

DRAIN_LIMIT = 4096          # bitta paketdagi maksimal piksel
_online_state = {"n": 0, "cd": cooldown_ms(0), "ro": False}


def readonly_now() -> bool:
    """"Faqat ko'rish" rejimi. Redis'dan online_loop har 2 soniyada yangilaydi,
    shuning uchun bo'yash yo'lida qo'shimcha so'rov yo'q."""
    return _online_state["ro"]


def online_now() -> int:
    return _online_state["n"]


def cooldown_now() -> int:
    return _online_state["cd"]


def pack_energy(energy: int, max_energy: int, next_ms: int) -> bytes:
    # max ham yuboriladi: do'st chaqirgan foydalanuvchining zaxirasi kattaroq
    return struct.pack("!BHHI", MSG_ENERGY, energy, max_energy, next_ms)


def pack_toast(text: str) -> bytes:
    return bytes([MSG_TOAST]) + text.encode("utf-8")


def pack_online(n: int, cd: int) -> bytes:
    return struct.pack("!BII", MSG_ONLINE, n, cd)


def pack_reject(x: int, y: int) -> bytes:
    return struct.pack("!BHH", MSG_REJECT, x, y)


async def _fanout(payload: bytes) -> None:
    """Paketni shu protsessdagi barcha socketlarga yuboradi."""
    dead = []
    for c in clients:
        try:
            await c.send(bytes_data=payload)
        except Exception:
            dead.append(c)
    for c in dead:
        clients.discard(c)


# --------------------------------------------------------------------------
# 1-vazifa: buferni bo'shatib, pub/sub'ga chiqarish
# --------------------------------------------------------------------------
async def drain_loop() -> None:
    interval = settings.BROADCAST_INTERVAL_MS / 1000
    r = store.aio
    while True:
        try:
            # LPOP count — bitta so'rovda 4096 tagacha paket
            chunk = await r.lpop(K_BUF, DRAIN_LIMIT)
            if chunk:
                payload = bytes([MSG_PIXELS]) + b"".join(chunk)
                await r.publish(K_PIXEL_CH, payload)
        except Exception:
            log.exception("drain_loop")
        await asyncio.sleep(interval)


# --------------------------------------------------------------------------
# 2-vazifa: pub/sub'dan o'qib, lokal socketlarga tarqatish
# --------------------------------------------------------------------------
async def subscribe_loop() -> None:
    while True:
        try:
            pubsub = store.aio.pubsub(ignore_subscribe_messages=True)
            await pubsub.subscribe(K_PIXEL_CH, K_ONLINE_CH)
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                data = msg["data"]
                if msg["channel"] == K_ONLINE_CH.encode():
                    _, n, cd = struct.unpack("!BII", data)
                    _online_state["n"], _online_state["cd"] = n, cd
                await _fanout(data)
        except Exception:
            log.exception("subscribe_loop")
            await asyncio.sleep(1)


# --------------------------------------------------------------------------
# 3-vazifa: onlayn sonini qayta hisoblash (klasterda bitta protsess)
# --------------------------------------------------------------------------
async def refresh_presence(now_ms: int) -> None:
    """Shu protsessdagi TIRIK socketlarni onlayn ro'yxatida yangilaydi.

    Nega mijoz ping'iga ishonmaymiz: brauzer fon tabdagi setInterval'ni
    60+ soniyaga sekinlashtiradi (Telegram Mini App va mobil brauzerlarda
    ayniqsa qattiq). 20 soniyalik ping kechikadi, 45 soniyalik TTL tugaydi
    va FAOL foydalanuvchi "offline" bo'lib qoladi — natijada onlayn soni
    tushib, kullaut noto'g'ri darajaga o'tadi.

    Server esa socket ochiqligini aniq biladi. Bitta ZADD bilan hammasini
    yangilaymiz: 1000 ta mijoz uchun ham 2 soniyada bitta so'rov.
    """
    mapping = {}
    for c in clients:
        uid = getattr(c, "uid", None)
        if uid is not None:
            mapping[str(uid)] = now_ms
    if mapping:
        await store.aio.zadd(K_ONLINE, mapping)


async def online_loop() -> None:
    interval = settings.ONLINE_REFRESH_MS / 1000
    r = store.aio
    lock_ttl = max(1000, settings.ONLINE_REFRESH_MS - 100)
    while True:
        try:
            now = int(time.time() * 1000)
            await refresh_presence(now)
            _online_state["ro"] = await store.is_readonly()

            # SET NX — faqat bitta protsess sanaydi, qolganlari pub/sub'dan oladi
            got = await r.set("mp:lock:online", b"1", nx=True, px=lock_ttl)
            if got:
                n = await store.recount_online(now)
                await r.publish(K_ONLINE_CH, pack_online(n, cooldown_ms(n)))
        except Exception:
            log.exception("online_loop")
        await asyncio.sleep(interval)


_tasks: list[asyncio.Task] = []


def start() -> None:
    """ASGI ishga tushganda bir marta chaqiriladi."""
    if _tasks:
        return
    loop = asyncio.get_event_loop()
    for fn in (drain_loop, subscribe_loop, online_loop):
        _tasks.append(loop.create_task(fn()))
    log.info("broadcaster ishga tushdi (%d ms batch)",
             settings.BROADCAST_INTERVAL_MS)
