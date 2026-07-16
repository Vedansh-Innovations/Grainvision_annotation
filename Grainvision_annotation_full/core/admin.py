from django.contrib import admin
from .models import Mandi, Commodity, AuditLog


@admin.register(Mandi)
class MandiAdmin(admin.ModelAdmin):
    list_display = ("name", "district", "state", "active")
    list_filter = ("state", "active")


@admin.register(Commodity)
class CommodityAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "active", "expected_min_count",
                    "expected_max_count", "target_samples", "extra_class_count")
    list_filter = ("active",)

    @admin.display(description="Extra classes")
    def extra_class_count(self, obj):
        extras = obj.extra_class_list
        return ", ".join(e["label"] for e in extras) if extras else "—"


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "entity_type", "entity_id", "ip_address")
    list_filter = ("action", "entity_type")
    search_fields = ("entity_id",)
    readonly_fields = [f.name for f in AuditLog._meta.fields]
