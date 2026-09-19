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
                msg = upd.get("message") or {}
                text = (msg.get("text") or "").strip()
                chat = (msg.get("chat") or {}).get("id")
                if not chat or not text.startswith("/start"):
                    continue
                call(token, "sendMessage",
                     chat_id=chat,
                     text=("Million Piksel — 1 000 000 pikselli umumiy doska.\n"
                           "Birgalikda rasm chizamiz. Boshlash uchun pastdagi "
                           "tugmani bosing."),
                     reply_markup={"inline_keyboard": [[
                         {"text": "Chizishni boshlash",
                          "web_app": {"url": url}}
                     ]]})
