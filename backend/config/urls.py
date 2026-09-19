import json
from functools import lru_cache

from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.templatetags.static import static
from django.urls import path

from canvas import adminpanel
from canvas.api import api, canvas_bin

FRONTEND = settings.BASE_DIR.parent / "frontend" / "index.html"


def _asset(name: str) -> str:
    """Statik faylning manzili, hash bilan.

    `collectstatic` qilinmagan bo'lsa `static()` ValueError tashlaydi va
    butun sayt 500 beradi. Bezak skript uchun bu juda qimmat narx —
    shuning uchun hash'siz manzilga tushib qolamiz. Sayt ishlaydi,
    faqat brauzer keshini yangilash uzoqroq davom etadi.
    """
    try:
        return static(name)
    except ValueError:
        return settings.STATIC_URL + name


@lru_cache(maxsize=1)
def _page() -> str:
    """frontend/index.html'ni o'zgartirmasdan ishlatamiz.

    Bitta fayl mustaqil ham ishlashi kerak (demo), shuning uchun uni
    ko'chirib yozish o'rniga serverda ikki joyini almashtiramiz.
    """
    html = FRONTEND.read_text(encoding="utf-8")
    html = html.replace("const DEMO       = true;", "const DEMO       = false;")

    config = json.dumps({
        "telegram_enabled": bool(settings.TELEGRAM_BOT_TOKEN),
        "bot_username": settings.TELEGRAM_BOT_USERNAME,
        "max_energy": settings.MAX_ENERGY,
        "canvas_size": settings.CANVAS_SIZE,
        "invite_bonus": settings.INVITE_BONUS_ENERGY,
    })
    html = html.replace(
        "</body>",
        f"<script>window.MP_CONFIG = {config};</script>\n"
        '<script src="https://telegram.org/js/telegram-web-app.js"></script>\n'
        # static() — ManifestStaticFilesStorage nomga hash qo'shadi,
        # shuning uchun yo'lni qo'lda yozib bo'lmaydi.
        f'<script src="{_asset("onboarding.js")}"></script>\n'
        f'<script src="{_asset("connect.js")}"></script>\n</body>',
    )
    return html


def app_view(request):
    html = _page() if not settings.DEBUG else _page.__wrapped__()
    return HttpResponse(html, content_type="text/html; charset=utf-8")


urlpatterns = [
    # Moderatsiya paneli — admin/ dan OLDIN, aks holda admin.site.urls
    # "panel/" ni o'zining model marshrutlari deb qabul qiladi.
    path("admin/panel/", adminpanel.panel, name="mp-panel"),
    path("admin/panel/api/stats", adminpanel.stats),
    path("admin/panel/api/reports", adminpanel.reports),
    path("admin/panel/api/report/status", adminpanel.report_status),
    path("admin/panel/api/pixel", adminpanel.pixel_history),
    path("admin/panel/api/region-authors", adminpanel.region_authors),
    path("admin/panel/api/rollback", adminpanel.rollback),
    path("admin/panel/api/players", adminpanel.players),
    path("admin/panel/api/ban", adminpanel.ban),
    path("admin/panel/api/unban", adminpanel.unban),
    path("admin/panel/api/logs", adminpanel.logs),
    path("admin/panel/api/timelapse", adminpanel.timelapse_list),
    path("admin/panel/api/timelapse/toggle", adminpanel.timelapse_toggle),
    path("admin/panel/api/timelapse/shoot", adminpanel.timelapse_shoot),
    path("admin/panel/api/teams", adminpanel.teams),

    path("admin/", admin.site.urls),
    path("api/canvas.bin", canvas_bin, name="canvas-bin"),
    path("api/", api.urls),
    path("", app_view, name="app"),
]

# Timelapse suratlari. Ishlab chiqarishda ularni nginx uzatadi
# (deploy/nginx.conf dagi /media/ bloki) — bu faqat lokal sinov uchun.
if settings.DEBUG:
    from django.conf.urls.static import static as static_urls
    urlpatterns += static_urls(settings.MEDIA_URL,
                               document_root=settings.MEDIA_ROOT)

admin.site.index_template = "admin/mp_index.html"
admin.site.site_header = "Million Piksel — moderatsiya"
admin.site.site_title = "Million Piksel"
admin.site.index_title = "Boshqaruv"
