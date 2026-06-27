"""Role-gating helpers implementing the PRD §13.3 authorisation matrix."""
from functools import wraps

from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import PermissionDenied


def _check(user, predicate):
    return user.is_authenticated and predicate(user)


def role_required(*predicates):
    """Allow access if the user satisfies ANY of the supplied predicates."""

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if any(_check(request.user, p) for p in predicates):
                return view_func(request, *args, **kwargs)
            raise PermissionDenied("Your role does not permit this action.")

        return _wrapped

    return decorator


# Convenient predicates
is_assayer = lambda u: u.is_assayer
is_qc = lambda u: u.is_qc
is_admin = lambda u: u.is_platform_admin
is_ml = lambda u: u.is_ml_engineer
is_qc_or_admin = lambda u: u.is_qc or u.is_platform_admin
is_ml_or_admin = lambda u: u.is_ml_engineer or u.is_platform_admin


class AssayerRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return _check(self.request.user, is_assayer)


class QCRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return _check(self.request.user, is_qc_or_admin)


class AdminRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return _check(self.request.user, is_admin)


class MLRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return _check(self.request.user, is_ml_or_admin)
