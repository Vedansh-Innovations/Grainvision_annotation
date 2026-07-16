from django import forms
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import User


class LoginForm(forms.Form):
    username = forms.CharField(
        widget=forms.TextInput(attrs={"placeholder": "Email or username", "autofocus": True})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "Password"})
    )


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("core:home")

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ident = form.cleaned_data["username"].strip()
        pwd = form.cleaned_data["password"]

        # Resolve by email or username.
        candidate = (
            User.objects.filter(email__iexact=ident).first()
            or User.objects.filter(username__iexact=ident).first()
        )

        if candidate and candidate.is_locked:
            mins = int((candidate.locked_until - timezone.now()).total_seconds() // 60) + 1
            messages.error(
                request,
                f"Account locked after repeated failures. Try again in {mins} minute(s).",
            )
            return render(request, "accounts/login.html", {"form": form})

        user = authenticate(request, username=candidate.username if candidate else ident, password=pwd)
        if user is not None and user.is_active:
            login(request, user)
            user.register_successful_login()
            return redirect(request.GET.get("next") or "core:home")

        if candidate:
            candidate.register_failed_login()
            remaining = max(0, 5 - candidate.failed_login_count)
            if candidate.is_locked:
                messages.error(request, "Account locked for 30 minutes after 5 failed attempts.")
            else:
                messages.error(request, f"Invalid credentials. {remaining} attempt(s) remaining.")
        else:
            messages.error(request, "Invalid credentials.")

    return render(request, "accounts/login.html", {"form": form})


def logout_view(request):
    logout(request)
    messages.info(request, "You have been signed out.")
    return redirect("accounts:login")
