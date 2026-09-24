"""ASGI kirish nuqtasi.

Broadcaster vazifalari (batching, pub/sub fan-out, onlayn hisob) shu yerda,
event loop ochilgandan keyin ishga tushadi.
"""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from django.core.asgi import get_asgi_application  # noqa: E402

from canvas import broadcaster  # noqa: E402
from canvas.redis_store import store  # noqa: E402
from canvas.routing import websocket_urlpatterns  # noqa: E402

store.ensure_canvas()

# Nazorat sozlamalarini DB'dan Redis'ga qayta yozamiz (Redis tozalangan bo'lsa
# ham jadval/cheksiz rejim yo'qolmasin). Baza hali tayyor bo'lmasa — o'tkazamiz.
try:
    from canvas import site as _site
    _site.mirror_control(_site.get_settings())
except Exception:
    pass

django_asgi = get_asgi_application()
_inner = ProtocolTypeRouter({
    "http": django_asgi,
    "websocket": URLRouter(websocket_urlpatterns),
})


async def application(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                broadcaster.start()
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    else:
        # Daphne lifespan yubormaydi — birinchi so'rovda ishga tushiramiz
        broadcaster.start()
        await _inner(scope, receive, send)
