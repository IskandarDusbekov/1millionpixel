"""Sayt sozlamalari, SEO va statik "meta" fayllar (robots, sitemap, manifest).

Sozlamalar admin panelda tahrirlanadi va DB'da (SiteSettings) turadi. Bosh
sahifa har so'rovda DB'ga bormasligi uchun ular jarayon ichida qisqa muddat
keshlanadi. Bir nechta worker bo'lgani uchun boshqa workerlarda o'zgarish
CACHE_SEC ichida ko'rinadi.
"""
from __future__ import annotations

import io
import json
import re
import threading
import time
from functools import lru_cache
from html import escape

from django.http import HttpResponse

from .models import SiteSettings
from .redis_store import K_READONLY, store

CACHE_SEC = 10
_lock = threading.Lock()
_cache: dict = {"at": -1e9, "obj": None}


def get_settings() -> SiteSettings:
    now = time.monotonic()
    obj = _cache["obj"]
    if obj is not None and now - _cache["at"] < CACHE_SEC:
        return obj
    with _lock:
        obj = SiteSettings.load()
        _cache.update(at=now, obj=obj)
        return obj


def invalidate() -> None:
    _cache.update(at=-1e9, obj=None)


def mirror_readonly(on: bool) -> None:
    """Consumer'lar DB'ga bormaydi — bayroq Redis'da turadi."""
    if on:
        store.sync.set(K_READONLY, b"1")
    else:
        store.sync.delete(K_READONLY)


def base_url(request, s: SiteSettings) -> str:
    if s.public_url:
        return s.public_url.rstrip("/")
    return request.build_absolute_uri("/").rstrip("/")


def absolute(request, s: SiteSettings, path: str) -> str:
    if not path:
        return ""
    if re.match(r"^https?://", path):
        return path
    return base_url(request, s) + (path if path.startswith("/") else "/" + path)


# --------------------------------------------------------------------------
# <head> ga yoziladigan SEO bloki
# --------------------------------------------------------------------------
def render_head(request, s: SiteSettings) -> str:
    e = lambda v: escape(str(v), quote=True)          # noqa: E731
    url = base_url(request, s) + "/"
    img = absolute(request, s, s.og_image)
    out = [
        f"<title>{e(s.seo_title)}</title>",
        f'<meta name="description" content="{e(s.seo_description)}">',
    ]
    if s.seo_keywords:
        out.append(f'<meta name="keywords" content="{e(s.seo_keywords)}">')
    out.append('<meta name="robots" content="%s">' % (
        "index, follow, max-image-preview:large" if s.robots_index
        else "noindex, nofollow"))
    out.append(f'<link rel="canonical" href="{e(url)}">')
    out.append(f'<meta name="theme-color" content="{e(s.theme_color)}">')
    out.append('<link rel="manifest" href="/manifest.webmanifest">')
    if s.favicon:
        out.append(f'<link rel="icon" href="{e(absolute(request, s, s.favicon))}">')
    out.append(f'<link rel="apple-touch-icon" href="/icon-192.png">')
    if s.google_verification:
        out.append('<meta name="google-site-verification" '
                   f'content="{e(s.google_verification)}">')
    if s.yandex_verification:
        out.append('<meta name="yandex-verification" '
                   f'content="{e(s.yandex_verification)}">')

    # Open Graph / Twitter — Telegram, WhatsApp, Facebook havola ko'rinishi
    og = {
        "og:type": "website", "og:site_name": s.site_name,
        "og:title": s.seo_title, "og:description": s.seo_description,
        "og:url": url, "og:locale": s.locale,
    }
    if img:
        og["og:image"] = img
    for k, v in og.items():
        out.append(f'<meta property="{k}" content="{e(v)}">')
    out.append('<meta name="twitter:card" content="%s">' % (
        "summary_large_image" if img else "summary"))
    out.append(f'<meta name="twitter:title" content="{e(s.seo_title)}">')
    out.append(f'<meta name="twitter:description" content="{e(s.seo_description)}">')
    if img:
        out.append(f'<meta name="twitter:image" content="{e(img)}">')
    if s.twitter:
        out.append(f'<meta name="twitter:site" content="{e(s.twitter)}">')

    ld = {
        "@context": "https://schema.org",
        "@type": "WebApplication",
        "name": s.site_name,
        "url": url,
        "description": s.seo_description,
        "applicationCategory": "GameApplication",
        "operatingSystem": "Any",
        "inLanguage": s.locale,
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "UZS"},
    }
    if img:
        ld["image"] = img
    # "</script>" ni buzmasligi uchun
    out.append('<script type="application/ld+json">%s</script>'
               % json.dumps(ld, ensure_ascii=False).replace("</", "<\\/"))
    return "\n".join(out)


def render_body(s: SiteSettings) -> str:
    """Qidiruv robotlari va ekran o'qigichlar uchun matn (ko'zga ko'rinmaydi)."""
    text = s.seo_text.strip() or s.seo_description
    return ('<main class="sr"><h1>%s</h1><p>%s</p></main>'
            % (escape(s.seo_title), escape(text)))


# --------------------------------------------------------------------------
# robots.txt / sitemap.xml / manifest / ikonkalar
# --------------------------------------------------------------------------
def robots_txt(request):
    s = get_settings()
    if s.robots_index:
        body = ("User-agent: *\nAllow: /\nDisallow: /admin/\n"
                "Disallow: /api/\nDisallow: /ws/\n\n"
                f"Sitemap: {base_url(request, s)}/sitemap.xml\n")
    else:
        body = "User-agent: *\nDisallow: /\n"
    return HttpResponse(body, content_type="text/plain; charset=utf-8")


def sitemap_xml(request):
    s = get_settings()
    if not s.robots_index:
        return HttpResponse(status=404)
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{escape(base_url(request, s))}/</loc>"
            f"<lastmod>{s.updated_at:%Y-%m-%d}</lastmod>"
            "<changefreq>always</changefreq><priority>1.0</priority></url>"
            "</urlset>")
    return HttpResponse(body, content_type="application/xml; charset=utf-8")


def manifest(request):
    s = get_settings()
    data = {
        "name": s.site_name,
        "short_name": s.site_name[:12],
        "description": s.seo_description,
        "start_url": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#EFE7D6",
        "theme_color": s.theme_color,
        "lang": s.locale,
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any maskable"},
        ],
    }
    return HttpResponse(json.dumps(data, ensure_ascii=False),
                        content_type="application/manifest+json")


# Standart ikonka: bosh sahifadagi 4 rangli piksel logotipi
_LOGO = [(1, 1, "#E50000"), (4, 1, "#0083C7"), (1, 4, "#E5D900"), (4, 4, "#02BE01")]


@lru_cache(maxsize=4)
def _icon_png(size: int) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (8, 8), "#FFFAF0")
    d = ImageDraw.Draw(img)
    for x, y, c in _LOGO:
        d.rectangle([x, y, x + 2, y + 2], fill=c)
    img = img.resize((size, size), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def icon(request, size: int):
    if size not in (32, 180, 192, 512):
        return HttpResponse(status=404)
    resp = HttpResponse(_icon_png(size), content_type="image/png")
    resp["Cache-Control"] = "public, max-age=86400"
    return resp
