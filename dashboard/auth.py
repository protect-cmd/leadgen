"""HTTP Basic Auth for the leadgen dashboard.

Two scopes: search (/) and queue (/queue), each with its own password
sourced from env. When the env var for a scope is unset, that scope's
dependency is a no-op — keeps tests open and makes local dev painless.

Any non-empty username is accepted; only the password is checked.
"""
from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

# auto_error=False so we can return a clear 401 with WWW-Authenticate when
# credentials are missing (HTTPBasic's default raises a JSON error).
_security = HTTPBasic(auto_error=False)

SEARCH_PASSWORD_ENV = "DASHBOARD_SEARCH_PASSWORD"
QUEUE_PASSWORD_ENV = "DASHBOARD_QUEUE_PASSWORD"


def _check_against(env_keys: list[str], creds: HTTPBasicCredentials | None) -> None:
    """Raise 401 unless creds.password matches one of the env passwords.

    If NONE of the env_keys resolve to a non-empty password, treat the
    route as open (used by tests and local dev where no password is set).
    """
    expected = [os.environ.get(k, "") for k in env_keys]
    expected = [p for p in expected if p]
    if not expected:
        return  # open mode — env not configured
    if not creds or not creds.password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password required",
            headers={"WWW-Authenticate": 'Basic realm="Dashboard"'},
        )
    for exp in expected:
        if secrets.compare_digest(creds.password, exp):
            return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid password",
        headers={"WWW-Authenticate": 'Basic realm="Dashboard"'},
    )


def require_search(creds: HTTPBasicCredentials | None = Depends(_security)) -> None:
    """Require the search-scope password (used for / and search APIs)."""
    _check_against([SEARCH_PASSWORD_ENV], creds)


def require_queue(creds: HTTPBasicCredentials | None = Depends(_security)) -> None:
    """Require the queue-scope password (used for /queue and queue APIs)."""
    _check_against([QUEUE_PASSWORD_ENV], creds)


def require_any(creds: HTTPBasicCredentials | None = Depends(_security)) -> None:
    """Accept either scope password (used for shared API endpoints)."""
    _check_against([SEARCH_PASSWORD_ENV, QUEUE_PASSWORD_ENV], creds)
