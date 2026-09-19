"""Dinamik kullaut — onlayn foydalanuvchilar soniga bog'liq.

Bitta joyda saqlanadi, chunki uni frontend ham, Lua skript ham,
WebSocket consumer ham ishlatadi. Ikkinchi nusxa paydo bo'lsa,
server bilan brauzer sanagichi bir-biriga mos kelmay qoladi.
"""

# (onlayn chegara, kullaut soniya) — o'sish tartibida
TIERS = (
    (10, 1),      # 0–10 kishi   -> 1 soniyada 1 piksel
    (100, 10),    # 10–100       -> 10 soniya
    (1000, 30),   # 100–1000     -> 30 soniya
)
MAX_COOLDOWN = 60  # 1000+ kishi -> 60 soniya


def cooldown_seconds(online: int) -> int:
    for limit, sec in TIERS:
        if online < limit:
            return sec
    return MAX_COOLDOWN


def cooldown_ms(online: int) -> int:
    return cooldown_seconds(online) * 1000
