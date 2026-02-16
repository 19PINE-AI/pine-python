"""
pine-ai — Pine AI SDK for Python.

Let Pine AI handle your digital chores.
Socket.IO + REST client for the Pine AI backend.
"""

from pine_ai.client import PineAI, AsyncPineAI
from pine_ai.auth import Auth
from pine_ai.sessions import SessionsAPI
from pine_ai.errors import PineAIError, AuthError, SessionError, ConnectionError
from pine_ai.models.events import C2SEvent, S2CEvent, NotificationEvent

__version__ = "0.1.0"
__all__ = [
    "PineAI",
    "AsyncPineAI",
    "Auth",
    "SessionsAPI",
    "PineAIError",
    "AuthError",
    "SessionError",
    "ConnectionError",
    "C2SEvent",
    "S2CEvent",
    "NotificationEvent",
]
