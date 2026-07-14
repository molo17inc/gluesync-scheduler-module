"""
Exceptions raised by the Chronos security module.

Route handlers should let FastAPI's ``HTTPException`` bubble up; the
exceptions defined here are for internal control-flow inside the
security package (introspector, cache).
"""

from __future__ import annotations


class ChronosSecurityError(Exception):
    """Base class for all security-related errors in Chronos."""


class UnauthenticatedError(ChronosSecurityError):
    """The caller could not be identified (missing / invalid token)."""


class PermissionDeniedError(ChronosSecurityError):
    """The caller is authenticated but lacks the required role."""


class IntrospectionError(ChronosSecurityError):
    """CoreHub introspection call failed for a non-auth reason.

    Examples: network error, 5xx from CoreHub, malformed response body.
    Callers must treat this as *fail closed* \u2014 return 401 to the
    client, never assume authorisation.
    """
