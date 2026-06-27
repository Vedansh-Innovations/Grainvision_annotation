from django.contrib import admin
from .models import Submission, Particle


class ParticleInline(admin.TabularInline):
    model = Particle
    extra = 0
    fields = ("particle_id", "label", "origin", "uncertain", "flagged_by_seg")
    readonly_fields = ("particle_id", "origin", "flagged_by_seg")


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("short_id", "commodity", "assayer", "status", "particle_count", "created_at")
    list_filter = ("status", "commodity")
    search_fields = ("id",)
    inlines = [ParticleInline]
    readonly_fields = ("id", "created_at", "updated_at")
