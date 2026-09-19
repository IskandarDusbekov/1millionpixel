"""Mavjud foydalanuvchilarga noyob chaqiruv kodini beradi.

Nega alohida migratsiya: `unique=True` maydonni callable default bilan
to'g'ridan-to'g'ri qo'shib bo'lmaydi — Django defaultni BIR MARTA hisoblab,
barcha satrlarga bir xil qiymat yozadi va unique cheklovi buziladi.
Shuning uchun uch qadam: (1) oddiy maydon, (2) shu yerda to'ldirish,
(3) unique qilish.
"""
from django.db import migrations


def fill(apps, schema_editor):
    from canvas.models import new_code

    Player = apps.get_model("canvas", "Player")
    used = set(
        Player.objects.exclude(invite_code=None)
        .values_list("invite_code", flat=True)
    )
    batch = []
    for player in Player.objects.filter(invite_code=None).only("id"):
        code = new_code()
        while code in used:
            code = new_code()
        used.add(code)
        player.invite_code = code
        batch.append(player)
    if batch:
        Player.objects.bulk_update(batch, ["invite_code"], batch_size=500)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [("canvas", "0002_teams_snapshots_invites")]

    operations = [migrations.RunPython(fill, noop)]
