"""Admin panel — moderatsiya shu yerdan bajariladi."""
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.db.models import Count
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from django.utils.html import format_html

from .models import (ModerationLog, PixelEvent, Player, Report, Snapshot,
                     Team)
from .moderation import ban_player, rollback_area, unban_player


class RollbackForm(forms.Form):
    x0 = forms.IntegerField(label="x0", min_value=0, max_value=999)
    y0 = forms.IntegerField(label="y0", min_value=0, max_value=999)
    x1 = forms.IntegerField(label="x1", min_value=0, max_value=999)
    y1 = forms.IntegerField(label="y1", min_value=0, max_value=999)
    minutes = forms.IntegerField(label="Necha daqiqa oldingi holat",
                                 initial=15, min_value=1, max_value=1440)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "member_count", "pixels_placed",
                    "owner", "created_at")
    search_fields = ("name", "code")
    readonly_fields = ("code", "pixels_placed", "created_at")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(n=Count("members"))

    @admin.display(description="A'zolar", ordering="n")
    def member_count(self, obj):
        return obj.n


@admin.register(Snapshot)
class SnapshotAdmin(admin.ModelAdmin):
    list_display = ("created_at", "preview", "reason", "fill", "changed",
                    "online", "size_kb")
    list_filter = ("reason", "created_at")
    date_hierarchy = "created_at"
    readonly_fields = ("file", "reason", "painted", "fill_percent", "changed",
                       "online", "bytes", "created_at", "big_preview")

    def has_add_permission(self, request):
        return False

    @admin.display(description="Surat")
    def preview(self, obj):
        return format_html(
            '<img src="{}{}" style="height:56px;image-rendering:pixelated;'
            'border:1px solid #ccc">', settings.MEDIA_URL, obj.file)

    @admin.display(description="Surat")
    def big_preview(self, obj):
        return format_html(
            '<img src="{}{}" style="width:520px;max-width:100%;'
            'image-rendering:pixelated;border:1px solid #ccc">',
            settings.MEDIA_URL, obj.file)

    @admin.display(description="To'lgan", ordering="fill_percent")
    def fill(self, obj):
        return f"{obj.fill_percent:.2f}%"

    @admin.display(description="Hajm", ordering="bytes")
    def size_kb(self, obj):
        return f"{obj.bytes / 1024:.0f} KB"


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ("id", "display_name", "username", "telegram_id", "team",
                    "pixels_placed", "invites_count", "last_ip", "is_banned",
                    "last_seen")
    list_filter = ("is_banned", "team")
    list_select_related = ("team",)
    search_fields = ("display_name", "username", "telegram_id", "google_sub",
                     "last_ip", "invite_code")
    readonly_fields = ("invite_code", "invites_count", "pixels_placed")
    actions = ("act_ban_24h", "act_ban_forever", "act_unban")

    @admin.action(description="Ban — 24 soat (IP bilan)")
    def act_ban_24h(self, request, queryset):
        for p in queryset:
            ban_player(p, 24, "admin panel", admin=request.user.username)
        self.message_user(request, f"{queryset.count()} ta hisob bloklandi")

    @admin.action(description="Ban — muddatsiz (IP bilan)")
    def act_ban_forever(self, request, queryset):
        for p in queryset:
            ban_player(p, None, "admin panel", admin=request.user.username)
        self.message_user(request, f"{queryset.count()} ta hisob bloklandi")

    @admin.action(description="Banni olib tashlash")
    def act_unban(self, request, queryset):
        for p in queryset:
            unban_player(p, admin=request.user.username)
        self.message_user(request, "Ban olib tashlandi")


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("id", "coords", "reporter", "status", "created_at")
    list_filter = ("status", "created_at")
    date_hierarchy = "created_at"
    actions = ("act_rollback_around", "act_done")

    @admin.display(description="Koordinata")
    def coords(self, obj):
        return f"{obj.x}, {obj.y}"

    @admin.action(description="Atrofdagi 100x100 ni 15 daqiqa orqaga qaytarish")
    def act_rollback_around(self, request, queryset):
        total = 0
        for r in queryset:
            res = rollback_area(r.x - 50, r.y - 50, r.x + 50, r.y + 50,
                                15, admin=request.user.username)
            total += res["changed"]
        queryset.update(status=Report.DONE)
        self.message_user(request, f"{total} ta piksel qaytarildi")

    @admin.action(description="Ko'rildi deb belgilash")
    def act_done(self, request, queryset):
        queryset.update(status=Report.DONE)


@admin.register(PixelEvent)
class PixelEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "x", "y", "color", "player")
    date_hierarchy = "created_at"
    search_fields = ("player__display_name",)
    list_select_related = ("player",)
    show_full_result_count = False        # jadval juda katta — COUNT(*) qilinmasin

    def has_add_permission(self, request):
        return False

    # ---------------- maxsus sahifa: hududni qaytarish ----------------
    def get_urls(self):
        return [
            path("rollback/", self.admin_site.admin_view(self.rollback_view),
                 name="canvas_rollback"),
        ] + super().get_urls()

    def rollback_view(self, request):
        if request.method == "POST":
            form = RollbackForm(request.POST)
            if form.is_valid():
                d = form.cleaned_data
                res = rollback_area(d["x0"], d["y0"], d["x1"], d["y1"],
                                    d["minutes"], admin=request.user.username)
                messages.success(
                    request,
                    f"{res['changed']} ta piksel {d['minutes']} daqiqa "
                    f"oldingi holatiga qaytarildi."
                )
                return HttpResponseRedirect(reverse("admin:canvas_rollback"))
        else:
            form = RollbackForm()
        return render(request, "admin/rollback.html", {
            "form": form, "title": "Hududni orqaga qaytarish",
            **self.admin_site.each_context(request),
        })


@admin.register(ModerationLog)
class ModerationLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "admin", "action", "detail")
    list_filter = ("action", "created_at")

    def has_add_permission(self, request):
        return False
