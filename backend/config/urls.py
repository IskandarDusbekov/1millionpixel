import json
import re
from functools import lru_cache

from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.templatetags.static import static
from django.urls import path, re_path
from django.views.decorators.clickjacking import xframe_options_exempt

from canvas import adminpanel, site
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
def _template() -> str:
    """frontend/index.html + skriptlar. SEO qismi har so'rovda qo'shiladi
    (u admin paneldan o'zgaradi), shuning uchun bu yerda faqat qolgan qism."""
    html = FRONTEND.read_text(encoding="utf-8")
    html = html.replace(
        "</body>",
        '<script src="https://telegram.org/js/telegram-web-app.js"></script>\n'
        # static() — ManifestStaticFilesStorage nomga hash qo'shadi,
        # shuning uchun yo'lni qo'lda yozib bo'lmaydi.
        f'<script src="{_asset("onboarding.js")}"></script>\n'
        f'<script src="{_asset("connect.js")}"></script>\n</body>',
    )
    return html


@xframe_options_exempt          # Telegram Web ilovani iframe ichida ochadi
def app_view(request):
    html = _template() if not settings.DEBUG else _template.__wrapped__()
    s = site.get_settings()
    eff = site.effective(s)

    config = json.dumps({
        "telegram_enabled": bool(settings.TELEGRAM_BOT_TOKEN),
        "bot_username": settings.TELEGRAM_BOT_USERNAME,
        "max_energy": settings.MAX_ENERGY,
        "canvas_size": settings.CANVAS_SIZE,
        "invite_bonus": settings.INVITE_BONUS_ENERGY,
        "site_name": s.site_name,
        "announcement": s.announcement if s.announcement_on else "",
        "readonly": eff["readonly"],
        "unlimited": eff["unlimited"],
    }).replace("</", "<\\/")

    # <title> va standart favicon o'rniga admin paneldagi qiymatlar qo'yiladi
    html = re.sub(r"<title>.*?</title>\s*", "", html, count=1, flags=re.S)
    if s.favicon:
        html = re.sub(r'<link rel="icon"[^>]*>\s*', "", html, count=1)
    html = html.replace("<!--MP:SEO-->", site.render_head(request, s), 1)
    html = html.replace("<!--MP:SEOBODY-->", site.render_body(s), 1)
    html = html.replace("<!--MP:CONFIG-->",
                        f"<script>window.MP_CONFIG = {config};</script>", 1)

    resp = HttpResponse(html, content_type="text/html; charset=utf-8")
    resp["Cache-Control"] = "no-cache"
    return resp


urlpatterns = [
    # Admin panel — admin/ dan OLDIN, aks holda admin.site.urls
    # "panel/" ni o'zining model marshrutlari deb qabul qiladi.
    path("admin/login/", adminpanel.login_view),          # Django admin kirishi ham shu
    path("admin/panel/login/", adminpanel.login_view, name="mp-login"),
    path("admin/panel/logout/", adminpanel.logout_view, name="mp-logout"),
    path("admin/panel/", adminpanel.panel, name="mp-panel"),
    path("admin/panel/api/stats", adminpanel.stats),
    path("admin/panel/api/series", adminpanel.series),
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
    path("admin/panel/api/site", adminpanel.site_get),
    path("admin/panel/api/site/save", adminpanel.site_save),
    path("admin/panel/api/files", adminpanel.files_list),
    path("admin/panel/api/files/upload", adminpanel.file_upload),
    path("admin/panel/api/files/delete", adminpanel.file_delete),
    path("admin/panel/api/password", adminpanel.password_change),

    path("admin/", admin.site.urls),
    path("api/canvas.bin", canvas_bin, name="canvas-bin"),
    path("api/", api.urls),

    # SEO va PWA
    path("robots.txt", site.robots_txt),
    path("sitemap.xml", site.sitemap_xml),
    path("manifest.webmanifest", site.manifest),
    path("icon-<int:size>.png", site.icon),
    re_path(r"^(?P<name>google[0-9a-f]{8,40}\.html)$", site.verify_file),
    path("", app_view, name="app"),
]

# Yuklangan fayllar va timelapse suratlari. Ishlab chiqarishda ularni nginx
# uzatadi (deploy/nginx.conf dagi /media/ bloki) — bu faqat lokal sinov uchun.
if settings.DEBUG:
    from django.conf.urls.static import static as static_urls
    urlpatterns += static_urls(settings.MEDIA_URL,
                               document_root=settings.MEDIA_ROOT)

# Django admin (model ro'yxatlari) ham faqat superuser uchun.
admin.site.has_permission = (
    lambda request: request.user.is_active and request.user.is_superuser)
admin.site.index_template = "admin/mp_index.html"
admin.site.site_header = "Million Piksel — moderatsiya"
admin.site.site_title = "Million Piksel"
admin.site.index_title = "Boshqaruv"
