"""Chizish nazorati: "faqat ko'rish", cheksiz chizish, jadval oynalari.

Sof funksiyalar (DB'siz): admin panel sozlamalarni Redis'ga JSON qilib yozadi
(`mp:ctl`), lock ushlab turgan worker har 2 soniyada uni vaqtga qarab hisoblab,
natijani (bayroqlar + kullaut) hamma workerga pub/sub orqali yuboradi.
Shuning uchun vaqt kelganda (masalan, oyna yopilganda) panelga tegish shart emas.

ctl = {"ro": bool, "unl": bool, "cd": soniya|0,
       "draw_from": ms|0, "draw_until": ms|0,
       "unl_from": ms|0, "unl_until": ms|0}
"""
from __future__ import annotations

FLAG_READONLY = 1
FLAG_UNLIMITED = 2


def _in_window(now_ms: int, start: int, end: int) -> bool:
    """Ikkala chegara ixtiyoriy: faqat boshi — o'shandan keyin, faqat oxiri — o'shangacha."""
    if start and now_ms < start:
        return False
    if end and now_ms > end:
        return False
    return True


def compute(ctl: dict | None, now_ms: int) -> tuple[bool, bool, int]:
    """(faqat_ko'rish, cheksiz, kullaut_ms yoki 0) — hozirgi samarali holat."""
    c = ctl or {}
    ro = bool(c.get("ro"))
    if c.get("draw_from") or c.get("draw_until"):
        if not _in_window(now_ms, c.get("draw_from", 0), c.get("draw_until", 0)):
            ro = True
    unl = bool(c.get("unl"))
    if c.get("unl_from") or c.get("unl_until"):
        if _in_window(now_ms, c.get("unl_from", 0), c.get("unl_until", 0)):
            unl = True
    cd = int(c.get("cd") or 0) * 1000
    return ro, unl, cd


def flags(ro: bool, unl: bool) -> int:
    return (FLAG_READONLY if ro else 0) | (FLAG_UNLIMITED if unl else 0)
