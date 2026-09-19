"""Statik fayllar saqlagichi.

Standart `ManifestStaticFilesStorage` manifestda yo'q fayl uchun
`ValueError` tashlaydi. Natijada bitta unutilgan `collectstatic` butun
saytni 500 xatosiga olib keladi — bezak fayl uchun juda qimmat narx.

`manifest_strict = False` bilan u hash'siz nomni qaytaradi: fayl
keshlanmaydi (bu unchalik muhim emas), lekin sayt ishlab turaveradi.
"""
from whitenoise.storage import CompressedManifestStaticFilesStorage


class ForgivingManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    manifest_strict = False
