"""
Sessions REST API — spec section 4.3.
"""

from typing import Any, Optional

from pine_ai.transport.http import HttpClient


class SessionsAPI:
    def __init__(self, http: HttpClient):
        self._http = http

    async def list(self, state: Optional[str] = None, limit: int = 30, offset: int = 0) -> dict[str, Any]:
        """List sessions — spec 4.3.1"""
        params = f"?limit={limit}&offset={offset}"
        if state:
            params += f"&state={state}"
        return await self._http.get(f"/v2/sessions{params}")

    async def get(self, session_id: str) -> dict[str, Any]:
        """Get session — spec 4.3.2"""
        return await self._http.get(f"/v2/sessions/{session_id}")

    async def create(self) -> dict[str, Any]:
        """Create session — spec 4.3.3"""
        return await self._http.post("/v2/sessions")

    async def delete(self, session_id: str, force_delete: bool = False) -> Any:
        """Delete session — spec 4.3.4"""
        params = {"force_delete": "true"} if force_delete else None
        return await self._http.delete(f"/v2/sessions/{session_id}", params=params)

    async def start_task(self, session_id: str) -> dict[str, Any]:
        """Start task — spec 4.3.9"""
        return await self._http.post(f"/v2/sessions/{session_id}/start")

    async def stop_task(self, session_id: str) -> dict[str, Any]:
        """Stop task — spec 4.3.10"""
        return await self._http.post(f"/v2/sessions/{session_id}/stop")

    async def update_scheduled_call_reminder(
        self, session_id: str, message_id: str, scheduled_time: str, enabled: bool,
    ) -> dict[str, Any]:
        """Update scheduled call reminder — spec 4.3.5"""
        return await self._http.put(f"/v2/sessions/{session_id}/scheduled-call-reminder", {
            "message_id": message_id,
            "scheduled_time": scheduled_time,
            "scheduled_call_reminder": enabled,
        })
