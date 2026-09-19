from django.urls import path

from .consumers import PixelConsumer

websocket_urlpatterns = [
    path("ws/canvas", PixelConsumer.as_asgi()),
]
