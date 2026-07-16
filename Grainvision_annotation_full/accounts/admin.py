from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "email", "get_full_name", "role", "is_active", "last_login_at")
    list_filter = ("role", "is_active")
    filter_horizontal = ("mandis", "groups", "user_permissions")
    fieldsets = UserAdmin.fieldsets + (
        ("GrainVision", {"fields": ("role", "mandis", "phone", "last_login_at")}),
    )
