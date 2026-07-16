"""Stashes request metadata on a thread-local so the audit log (PRD §11.1
`audit_log`) can record ip_address / user_agent without threading them through
every call site."""
import threading

_local = threading.local()


def get_audit_context():
    return getattr(_local, "ctx", {"ip_address": None, "user_agent": ""})


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
        ip = xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")
        _local.ctx = {
            "ip_address": ip,
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:512],
        }
        try:
            return self.get_response(request)
        finally:
            _local.ctx = {"ip_address": None, "user_agent": ""}
