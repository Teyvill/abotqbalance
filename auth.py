"""Shared authentication and role helpers.

Kept separate from app.py so blueprints (e.g. the Book of Tales editor) can
reuse role gating without importing the application module.
"""

import hmac
import os
from functools import wraps

from flask import abort, session

# Higher number = more privileges. Each role includes everything below it.
ROLE_LEVELS = {"reader": 1, "editor": 2, "admin": 3}


def load_credentials():
    """Read the configured login/password for each role from the environment.

    A role is only usable if both its *_LOGIN and *_PASSWORD vars are set.
    """
    creds = {}
    for role in ROLE_LEVELS:
        login = os.environ.get(f"{role.upper()}_LOGIN")
        password = os.environ.get(f"{role.upper()}_PASSWORD")
        if login and password:
            creds[role] = (login, password)
    return creds


def authenticate(login, password):
    """Return the role matching these credentials, or None.

    Uses constant-time comparison to avoid leaking timing information.
    """
    for role, (expected_login, expected_password) in load_credentials().items():
        login_ok = hmac.compare_digest(login, expected_login)
        password_ok = hmac.compare_digest(password, expected_password)
        if login_ok and password_ok:
            return role
    return None


def has_role(min_role):
    """True if the logged-in user's role is at least `min_role`."""
    role = session.get("role")
    return bool(role) and ROLE_LEVELS.get(role, 0) >= ROLE_LEVELS[min_role]


def require_role(min_role):
    """Decorator: abort with 403 unless the user has at least `min_role`."""

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not has_role(min_role):
                abort(403)
            return view(*args, **kwargs)

        return wrapper

    return decorator
