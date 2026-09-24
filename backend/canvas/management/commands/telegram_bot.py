"""Telegram bot — Mini App'ni ochadi va menyu tugmasini sozlaydi.

Qo'shimcha kutubxona kerak emas: Bot API bilan oddiy HTTP (long polling)
orqali ishlaydi.

Ishga tushirish:
    python manage.py telegram_bot --url https://millionpixel.uz

Bir martalik sozlash (keyin bot ishlab turmasa ham menyu tugmasi qoladi):
    python manage.py telegram_bot --url https://millionpixel.uz --setup-only
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from canvas.redis_store import k_botlogin, store

API = "https://api.telegram.org/bot{token}/{method}"


def call(token: str, method: str, **params):
    url = API.format(token=token, method=method)
    data = json.dumps(params).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=65) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read() or b'{"ok":false}')


class Command(BaseCommand):
    help = "Telegram bot: Mini App tugmasi va /start javobi"

    def add_arguments(self, parser):
        parser.add_argument("--url", required=True,
                            help="Mini App manzili, https:// bilan")
        parser.add_argument("--setup-only", action="store_true",
                            help="Faqat menyu tugmasini sozlab chiqadi")

    def handle(self, *args, **opts):
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            raise CommandError("TELEGRAM_BOT_TOKEN sozlanmagan (.env)")
        url = opts["url"]
        if not url.startswith("https://"):
            raise CommandError("Telegram faqat https:// manzilni qabul qiladi")

        me = call(token, "getMe")
        if not me.get("ok"):
            raise CommandError(f"Token noto'g'ri: {me}")
        self.stdout.write(self.style.SUCCESS(
            f"Bot: @{me['result']['username']}"))

        # Chat oynasidagi doimiy menyu tugmasi
        call(token, "setChatMenuButton", menu_button={
            "type": "web_app",
            "text": "Chizish",
            "web_app": {"url": url},
        })
        call(token, "setMyCommands", commands=[
            {"command": "start", "description": "Million Piksel'ni ochish"},
        ])
        self.stdout.write(self.style.SUCCESS("Menyu tugmasi sozlandi"))

        if opts["setup_only"]:
            return

        self.stdout.write("Long polling boshlandi (Ctrl+C — to'xtatish)")
        offset = 0
        while True:
            try:
                resp = call(token, "getUpdates", offset=offset, timeout=50)
            except Exception as exc:                      # tarmoq uzilishi
                self.stderr.write(f"getUpdates: {exc}")
                time.sleep(3)
                continue

            for upd in resp.get("result", []):
                offset = upd["update_id"] + 1
                try:
                    if upd.get("callback_query"):
                        self.on_callback(token, upd["callback_query"])
                    elif upd.get("message"):
                        self.on_message(token, url, upd["message"])
                except Exception as exc:                  # bitta xato botni yiqitmasin
                    self.stderr.write(f"update {upd.get('update_id')}: {exc}")

    # ------------------------------------------------------------ xabarlar
    def on_message(self, token: str, url: str, msg: dict):
        text = (msg.get("text") or "").strip()
        chat = (msg.get("chat") or {}).get("id")
        if not chat or not text.startswith("/start"):
            return

        param = text[len("/start"):].strip()
        if param.startswith("login_"):
            return self.ask_confirm(token, msg, param[len("login_"):])

        call(token, "sendMessage",
             chat_id=chat,
             text=("Million Piksel — 1 000 000 pikselli umumiy doska.\n"
                   "Birgalikda rasm chizamiz. Boshlash uchun pastdagi "
                   "tugmani bosing."),
             reply_markup={"inline_keyboard": [[
                 {"text": "Chizishni boshlash", "web_app": {"url": url}}
             ]]})

    # ------------------------------------------- brauzerdan kirishni tasdiqlash
    # Nega tugma? Kodni boshqa odam yaratib, sizga yuborishi mumkin: "Start"
    # bosgan zahoti u sizning hisobingizga kirib olardi. Shuning uchun so'rov
    # qayerdan kelganini (IP, qurilma) ko'rsatamiz va faqat ochiq tasdiq bilan
    # kiritamiz.
    def ask_confirm(self, token: str, msg: dict, code: str):
        chat = msg["chat"]["id"]
        meta = store.botlogin_get_sync(code) if code.isalnum() else None
        if not meta or meta.get("s") != "p":
            call(token, "sendMessage", chat_id=chat,
                 text="Bu havola eskirgan. Saytda «Bot orqali kirish» ni qayta bosing.")
            return
        call(token, "sendMessage", chat_id=chat,
             text=("Million Piksel — saytga kirish so'rovi\n\n"
                   f"Qurilma: {meta.get('ua') or '—'}\n"
                   f"IP: {meta.get('ip') or '—'}\n\n"
                   "Bu so'rovni SIZ boshlagan bo'lsangizgina tasdiqlang."),
             reply_markup={"inline_keyboard": [[
                 {"text": "Tasdiqlayman", "callback_data": f"ok:{code}"},
                 {"text": "Bekor qilish", "callback_data": f"no:{code}"},
             ]]})

    def on_callback(self, token: str, cq: dict):
        data = cq.get("data") or ""
        chat = (cq.get("message") or {}).get("chat", {}).get("id")
        msg_id = (cq.get("message") or {}).get("message_id")
        who = cq.get("from") or {}
        answer = "Xato"

        action, _, code = data.partition(":")
        if code.isalnum() and action == "ok" and who.get("id"):
            ok = store.botlogin_confirm_sync(code, {
                "telegram_id": who["id"],
                "username": who.get("username") or "",
                "display_name": " ".join(
                    p for p in (who.get("first_name"), who.get("last_name")) if p),
            })
            answer = ("Tayyor! Saytga qayting — kirish avtomatik bo'ladi."
                      if ok else "Havola eskirgan, saytda qayta boshlang.")
        elif action == "no":
            if code.isalnum():
                store.key_delete_sync(k_botlogin(code))
            answer = "Bekor qilindi."

        call(token, "answerCallbackQuery", callback_query_id=cq["id"])
        if chat and msg_id:
            call(token, "editMessageText", chat_id=chat, message_id=msg_id,
                 text=answer)
