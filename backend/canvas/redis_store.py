"""Redis qatlami — kanvas holati, energiya, onlayn hisob, broadcast buferi.

Nega Redis'da? 1 000 000 piksel = 1 MB satr. Har bo'yash — bitta SETRANGE,
ya'ni O(1). PostgreSQL bunday tezlikka (sekundiga minglab yozuv) mos emas,
shuning uchun u faqat TARIX uchun ishlatiladi (rollback/moderatsiya).

Eng muhim joyi — PLACE_LUA. Energiya tekshiruvi, kamaytirish va pikselni
yozish BITTA atomar qadamda bajarilishi shart. Aks holda bir foydalanuvchi
ikkita ulanishdan bir vaqtda so'rov yuborib, energiyani chetlab o'ta oladi
(klassik race condition).
"""
from __future__ import annotations

import json

import redis
import redis.asyncio as aioredis
from django.conf import settings

N = settings.CANVAS_SIZE
CANVAS_BYTES = N * N

K_CANVAS = "mp:canvas"          # 1 MB satr, 1 bayt = 1 piksel (palitra indeksi)
K_BUF = "mp:buf"                # broadcast buferi (5 baytlik paketlar)
K_HIST = "mp:hist"              # Redis Stream -> PostgreSQL'ga oqadi
K_ONLINE = "mp:online"          # ZSET: user_id -> oxirgi heartbeat (ms)
K_ONLINE_N = "mp:online:n"      # keshlangan son
K_PIXEL_CH = "mp:ch:pixels"     # pub/sub: piksel paketlari
K_ONLINE_CH = "mp:ch:online"    # pub/sub: onlayn soni
K_PLACED = "mp:placed"          # jami qo'yilgan piksel (timelapse faolligi)
K_TIMELAPSE_ON = "mp:timelapse:on"   # surat olish yoqilganmi
K_READONLY = "mp:readonly"      # "faqat ko'rish" rejimi (admin paneldan)


def k_botlogin(code: str) -> str:
    """Telegram bot orqali kirish: kod -> (kutilmoqda | foydalanuvchi JSON)."""
    return f"mp:botlogin:{code}"


def k_energy(uid: int) -> str:
    return f"mp:e:{uid}"


def k_maxe(uid: int) -> str:
    """Foydalanuvchining shaxsiy zaxira chegarasi (do'st chaqirish bonusi)."""
    return f"mp:maxe:{uid}"


def k_ban_user(uid: int) -> str:
    return f"mp:ban:u:{uid}"


def k_ban_ip(ip: str) -> str:
    return f"mp:ban:ip:{ip}"


# --------------------------------------------------------------------------
# Atomar piksel qo'yish
# --------------------------------------------------------------------------
PLACE_LUA = """
local canvas, ekey, buf, hist, placed = KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5]
local off = tonumber(ARGV[1])
local col = ARGV[2]            -- aynan 1 bayt
local now = tonumber(ARGV[3])
local cd  = tonumber(ARGV[4])
local mx  = tonumber(ARGV[5])
local pkt = ARGV[6]            -- 5 bayt: uint16 x, uint16 y, uint8 c
local uid = ARGV[7]

local e  = tonumber(redis.call('HGET', ekey, 'e'))
local at = tonumber(redis.call('HGET', ekey, 'at'))
if e == nil or at == nil then e = mx; at = now end

if e >= mx then
  -- zaxira to'la edi: sanagich aynan shu bo'yashdan boshlanadi
  e = mx
  at = now
else
  local g = math.floor((now - at) / cd)
  if g > 0 then
    if e + g >= mx then
      e = mx; at = now
    else
      -- yarim yig'ilgan vaqt yo'qolmasin: at ni faqat butun qadamga suramiz
      e = e + g; at = at + g * cd
    end
  end
end

if e < 1 then
  return {0, e, cd - ((now - at) % cd)}
end

e = e - 1
redis.call('HSET', ekey, 'e', e, 'at', at)
-- 'n' = shu foydalanuvchi qo'ygan piksellar soni. Chaqiruv bonusi shunga
-- qaraydi, shuning uchun u PostgreSQL'ga (drain_history) bog'liq emas.
redis.call('HINCRBY', ekey, 'n', 1)
redis.call('EXPIRE', ekey, 604800)
redis.call('SETRANGE', canvas, off, col)
redis.call('RPUSH', buf, pkt)
redis.call('INCR', placed)     -- timelapse faollikni shundan biladi
redis.call('XADD', hist, 'MAXLEN', '~', 2000000, '*',
           'u', uid, 'o', off, 'c', string.byte(col), 't', now)

local nxt = 0
if e < mx then nxt = cd - ((now - at) % cd) end
return {1, e, nxt}
"""


class Store:
    """Sinxron va asinxron klientlarni bitta joyda saqlaydi."""

    def __init__(self):
        if getattr(settings, "FAKE_REDIS", False):
            # Lokal sinov: Windows'da Redis o'rnatmasdan ishga tushirish uchun.
            # Ikkala klient BITTA server nusxasini bo'lishadi, aks holda
            # pub/sub va kanvas bir-birini ko'rmaydi.
            import fakeredis
            server = fakeredis.FakeServer()
            self.sync = fakeredis.FakeStrictRedis(
                server=server, decode_responses=False)
            self.aio = fakeredis.FakeAsyncRedis(
                server=server, decode_responses=False)
        else:
            self.sync = redis.from_url(settings.REDIS_URL, decode_responses=False)
            self.aio = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
        self._place_sync = self.sync.register_script(PLACE_LUA)
        self._place_aio = self.aio.register_script(PLACE_LUA)

    # -------------------------------------------------- kanvas
    def ensure_canvas(self) -> None:
        """1 MB satrni bir marta yaratadi. 0 bayt = palitra[0] = oq."""
        if self.sync.strlen(K_CANVAS) < CANVAS_BYTES:
            self.sync.setrange(K_CANVAS, CANVAS_BYTES - 1, b"\x00")

    def snapshot(self) -> bytes:
        """Butun doska — yangi ulangan foydalanuvchiga beriladi."""
        data = self.sync.get(K_CANVAS) or b""
        if len(data) < CANVAS_BYTES:
            data = data + b"\x00" * (CANVAS_BYTES - len(data))
        return data

    def read_rect(self, x0, y0, x1, y1) -> list[bytes]:
        """Hudud — satrma-satr. Moderatsiya uchun."""
        pipe = self.sync.pipeline(transaction=False)
        for y in range(y0, y1 + 1):
            off = y * N + x0
            pipe.getrange(K_CANVAS, off, off + (x1 - x0))
        return pipe.execute()

    def write_row(self, x0: int, y: int, row: bytes) -> None:
        self.sync.setrange(K_CANVAS, y * N + x0, row)

    # -------------------------------------------------- piksel qo'yish
    async def place(self, uid: int, x: int, y: int, color: int,
                    now_ms: int, cooldown_ms: int,
                    max_energy: int | None = None) -> tuple[bool, int, int]:
        """(ruxsat berildi, qolgan energiya, keyingi piksel ms) qaytaradi."""
        off = y * N + x
        pkt = (x.to_bytes(2, "big") + y.to_bytes(2, "big")
               + bytes([color]))
        ok, energy, nxt = await self._place_aio(
            keys=[K_CANVAS, k_energy(uid), K_BUF, K_HIST, K_PLACED],
            args=[off, bytes([color]), now_ms, cooldown_ms,
                  max_energy or settings.MAX_ENERGY, pkt, uid],
            client=self.aio,
        )
        return bool(ok), int(energy), int(nxt)

    async def max_energy(self, uid: int) -> int:
        """Shaxsiy chegara (bonus bilan). Kirishda yoziladi, bu yerda o'qiladi."""
        v = await self.aio.get(k_maxe(uid))
        return int(v) if v else settings.MAX_ENERGY

    def set_max_energy_sync(self, uid: int, value: int) -> None:
        self.sync.set(k_maxe(uid), int(value))

    def placed_by_sync(self, uids: list[int]) -> dict[int, int]:
        """Har bir foydalanuvchi qo'ygan piksellar soni (bitta pipeline)."""
        if not uids:
            return {}
        pipe = self.sync.pipeline(transaction=False)
        for uid in uids:
            pipe.hget(k_energy(uid), "n")
        return {uid: int(v) if v else 0
                for uid, v in zip(uids, pipe.execute())}

    async def energy(self, uid: int, now_ms: int, cooldown_ms: int,
                     max_energy: int | None = None) -> tuple[int, int]:
        """Sarflamasdan joriy energiyani hisoblaydi (ulanish paytida)."""
        vals = await self.aio.hmget(k_energy(uid), "e", "at")
        mx = max_energy or settings.MAX_ENERGY
        if not vals[0] or not vals[1]:
            return mx, 0
        e, at = int(vals[0]), int(vals[1])
        if e >= mx:
            return mx, 0
        e = min(mx, e + (now_ms - at) // cooldown_ms)
        if e >= mx:
            return mx, 0
        return e, cooldown_ms - ((now_ms - at) % cooldown_ms)

    # -------------------------------------------------- onlayn
    async def heartbeat(self, uid: int, now_ms: int) -> None:
        await self.aio.zadd(K_ONLINE, {str(uid): now_ms})

    async def leave(self, uid: int) -> None:
        await self.aio.zrem(K_ONLINE, str(uid))

    async def recount_online(self, now_ms: int) -> int:
        """Eskirgan yozuvlarni tozalab, aniq sonni qaytaradi."""
        cutoff = now_ms - settings.ONLINE_TTL_SEC * 1000
        pipe = self.aio.pipeline(transaction=False)
        pipe.zremrangebyscore(K_ONLINE, 0, cutoff)
        pipe.zcard(K_ONLINE)
        _, n = await pipe.execute()
        await self.aio.set(K_ONLINE_N, n)
        return int(n)

    async def online_cached(self) -> int:
        v = await self.aio.get(K_ONLINE_N)
        return int(v) if v else 0

    def online_cached_sync(self) -> int:
        v = self.sync.get(K_ONLINE_N)
        return int(v) if v else 0

    # -------------------------------------------------- umumiy yordamchilar
    def rate_hit_sync(self, key: str, window_sec: int) -> int:
        """Oynadagi urinishlar sonini oshiradi va yangi qiymatni qaytaradi."""
        n = self.sync.incr(key)
        if n == 1:
            self.sync.expire(key, window_sec)
        return int(n)

    def rate_get_sync(self, key: str) -> int:
        v = self.sync.get(key)
        return int(v) if v else 0

    def key_delete_sync(self, key: str) -> None:
        self.sync.delete(key)

    async def is_readonly(self) -> bool:
        return bool(await self.aio.exists(K_READONLY))

    # -------------------------------------------------- bot orqali kirish
    # Yozuv JSON: {"s": "p", ip, ua}  — brauzer kutmoqda,
    #             {"s": "ok", telegram_id, ...} — bot tasdiqladi.
    def botlogin_create_sync(self, code: str, meta: dict, ttl: int = 300) -> None:
        self.sync.set(k_botlogin(code), json.dumps({"s": "p", **meta}).encode(),
                      ex=ttl)

    def botlogin_get_sync(self, code: str) -> dict | None:
        v = self.sync.get(k_botlogin(code))
        return json.loads(v) if v else None

    def botlogin_confirm_sync(self, code: str, info: dict, ttl: int = 120) -> bool:
        """Faqat KUTILAYOTGAN kodni tasdiqlaydi (yo'q kodga yozmaydi)."""
        cur = self.botlogin_get_sync(code)
        if not cur or cur.get("s") != "p":
            return False
        return bool(self.sync.set(k_botlogin(code),
                                  json.dumps({"s": "ok", **info}).encode(),
                                  xx=True, ex=ttl))

    def botlogin_take_sync(self, code: str) -> dict | None:
        """None — kod yo'q/eskirgan; {"s":"p"} — kutilmoqda;
        {"s":"ok",...} — tasdiqlangan (bir marta beriladi va o'chiriladi)."""
        key = k_botlogin(code)
        v = self.sync.get(key)
        if not v:
            return None
        cur = json.loads(v)
        if cur.get("s") != "ok":
            return cur
        pipe = self.sync.pipeline(transaction=True)
        pipe.get(key)
        pipe.delete(key)
        got, _ = pipe.execute()
        return json.loads(got) if got else None

    # -------------------------------------------------- ban
    async def is_banned(self, uid: int, ip: str) -> bool:
        pipe = self.aio.pipeline(transaction=False)
        pipe.exists(k_ban_user(uid))
        pipe.exists(k_ban_ip(ip))
        a, b = await pipe.execute()
        return bool(a or b)

    def ban_sync(self, uid: int | None, ip: str | None, seconds: int | None):
        pipe = self.sync.pipeline()
        for key in (k_ban_user(uid) if uid else None,
                    k_ban_ip(ip) if ip else None):
            if not key:
                continue
            pipe.set(key, b"1")
            if seconds:
                pipe.expire(key, seconds)
        pipe.execute()

    def unban_sync(self, uid: int | None, ip: str | None):
        keys = [k for k in (k_ban_user(uid) if uid else None,
                            k_ban_ip(ip) if ip else None) if k]
        if keys:
            self.sync.delete(*keys)


store = Store()
