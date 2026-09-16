"""Single-user login with CSRF protection and a simple login throttle."""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from functools import wraps

from flask import abort, current_app, redirect, request, session, url_for
from werkzeug.security import check_password_hash

MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60

# A throwaway hash so a wrong username costs as much time as a wrong password.
_DUMMY_HASH = "scrypt:32768:8:1$invalidsaltvalue$" + "0" * 128


class LoginThrottle:
    """In-memory per-client failure counter. Gunicorn runs one worker, so this is shared."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, tuple[int, float]] = {}

    def locked(self, key: str) -> bool:
        with self._lock:
            count, since = self._failures.get(key, (0, 0.0))
            if count >= MAX_FAILURES and time.monotonic() - since < LOCKOUT_SECONDS:
                return True
            if count >= MAX_FAILURES:
                del self._failures[key]
            return False

    def fail(self, key: str) -> None:
        with self._lock:
            count, _ = self._failures.get(key, (0, 0.0))
            self._failures[key] = (count + 1, time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


throttle = LoginThrottle()


def client_key() -> str:
    # The app only listens on loopback behind Cloudflare Tunnel, so this header is set by Cloudflare.
    return request.headers.get("CF-Connecting-IP") or request.remote_addr or "unknown"


def check_credentials(username: str, password: str) -> bool:
    expected_user = current_app.config["AUTH_USERNAME"]
    password_hash = current_app.config["AUTH_PASSWORD_HASH"]
    user_ok = hmac.compare_digest(username.encode(), expected_user.encode())
    try:
        password_ok = check_password_hash(password_hash if user_ok else _DUMMY_HASH, password)
    except ValueError:
        password_ok = False
    return user_ok and password_ok


def login_user(username: str) -> None:
    session.clear()
    session["user"] = username
    session["csrf"] = secrets.token_urlsafe(32)
    session.permanent = True


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf() -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    sent = request.form.get("csrf_token", "")
    expected = session.get("csrf", "")
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, "CSRF-Token ungültig. Seite neu laden und erneut versuchen.")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("web.login", next=request.full_path if request.query_string else request.path))
        return view(*args, **kwargs)

    return wrapped
