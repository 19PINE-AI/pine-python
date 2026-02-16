"""
Chat engine — send messages and yield complete events via async generator.

Stream buffering:
- Tier 1: session:text_part buffered until final: true
- Tier 2: Non-streaming events yield immediately
- Tier 3: session:work_log_part debounced (3s silence)
"""

import asyncio
from typing import Any, AsyncGenerator, Optional

from pine_ai.models.events import C2SEvent, S2CEvent
from pine_ai.transport.socketio import SocketIOManager

# Immediate-dispatch event types (Tier 2)
IMMEDIATE_EVENTS = {
    S2CEvent.SESSION_STATE, S2CEvent.SESSION_INPUT_STATE, S2CEvent.SESSION_RICH_CONTENT,
    S2CEvent.SESSION_FORM_TO_USER,
    S2CEvent.SESSION_ASK_FOR_LOCATION, S2CEvent.SESSION_LOCATION_SELECTION,
    S2CEvent.SESSION_REWARD, S2CEvent.SESSION_PAYMENT, S2CEvent.SESSION_TASK_READY,
    S2CEvent.SESSION_TASK_FINISHED, S2CEvent.SESSION_INTERACTIVE_AUTH_CONFIRMATION,
    S2CEvent.SESSION_THREE_WAY_CALL, S2CEvent.SESSION_ERROR, S2CEvent.SESSION_THINKING,
    S2CEvent.SESSION_WORK_LOG, S2CEvent.SESSION_UPDATE_TITLE, S2CEvent.SESSION_TEXT,
    S2CEvent.SESSION_MESSAGE_STATUS, S2CEvent.SESSION_CARD, S2CEvent.SESSION_NEXT_TASKS,
    S2CEvent.SESSION_CONTINUE_IN_NEW_TASK, S2CEvent.SESSION_SOCIAL_SHARING,
    S2CEvent.SESSION_RETRY, S2CEvent.SESSION_DEBUG, S2CEvent.SESSION_ACTION_STATUS,
    S2CEvent.SESSION_COMPUTER_USE_INTERVENTION,
}


class ChatEvent:
    __slots__ = ("type", "session_id", "message_id", "data", "metadata")

    def __init__(self, type: str, session_id: str, data: Any,
                 message_id: Optional[str] = None, metadata: Optional[dict[str, Any]] = None):
        self.type = type
        self.session_id = session_id
        self.message_id = message_id
        self.data = data
        self.metadata = metadata

    def __repr__(self) -> str:
        return f"ChatEvent(type={self.type!r}, session_id={self.session_id!r})"


class TextPartBuffer:
    """Buffer text_part chunks per message_id, flush on final: true."""

    def __init__(self) -> None:
        self._parts: dict[str, list[str]] = {}

    def collect(self, message_id: str, content: str, final: bool) -> Optional[str]:
        if message_id not in self._parts:
            self._parts[message_id] = []
        if content:
            self._parts[message_id].append(content)
        if final:
            merged = "".join(self._parts.pop(message_id, []))
            return merged
        return None


class ChatEngine:
    def __init__(self, sio: SocketIOManager):
        self._sio = sio

    async def join_session(self, session_id: str) -> dict[str, Any]:
        """Join a session room — spec 5.1.1.
        Production handler reads payload.session_id (set by envelope builder).
        """
        return await self._sio.emit_and_wait(
            C2SEvent.SESSION_JOIN,
            None,  # payload.data is not used for join
            session_id=session_id,
        )

    def leave_session(self, session_id: str) -> None:
        """Leave a session room."""
        self._sio.emit(C2SEvent.SESSION_LEAVE, None, session_id)

    async def chat(
        self, session_id: str, content: str,
    ) -> AsyncGenerator[ChatEvent, None]:
        """Send a message and yield events with stream buffering.
        Production handler reads payload.data as {content, attachments, ...}.
        """
        from datetime import datetime
        self._sio.emit(
            C2SEvent.SESSION_MESSAGE,
            {
                "content": content,
                "attachments": [],
                "referenced_sessions": [],
                "client_now_date": datetime.now().isoformat(),
            },
            session_id,
        )
        async for event in self._listen(session_id):
            yield event

    async def _listen(self, session_id: str) -> AsyncGenerator[ChatEvent, None]:
        """Listen for events with stream buffering."""
        text_buffer = TextPartBuffer()
        queue: asyncio.Queue[Optional[ChatEvent]] = asyncio.Queue()
        done = False
        # Only terminate on waiting_input AFTER agent has sent substantive content.
        # The initial waiting_input (default state) arrives before agent starts.
        received_agent_response = False

        # Work log debounce state
        wl_timers: dict[str, asyncio.TimerHandle] = {}
        wl_buffers: dict[str, dict[str, Any]] = {}

        def flush_wl(step_id: str) -> None:
            buf = wl_buffers.pop(step_id, None)
            wl_timers.pop(step_id, None)
            if buf:
                queue.put_nowait(ChatEvent(
                    type=S2CEvent.SESSION_WORK_LOG_PART,
                    session_id=session_id,
                    data={"step_id": step_id, "text": buf.get("text", ""), "status": buf.get("status")},
                ))

        def handler(event: str, raw: dict[str, Any]) -> None:
            nonlocal done
            payload = raw.get("payload", {})
            p_session_id = payload.get("session_id")
            if p_session_id and p_session_id != session_id:
                return  # Not our session — other handlers will process it

            message_id = payload.get("message_id")
            data = payload.get("data")
            metadata = raw.get("metadata")

            # Tier 1: text_part
            if event == S2CEvent.SESSION_TEXT_PART:
                if isinstance(data, dict):
                    content = data.get("content", "")
                    final = data.get("final", False)
                    merged = text_buffer.collect(message_id or "unknown", content, final)
                    if merged is not None:
                        queue.put_nowait(ChatEvent(
                            type=S2CEvent.SESSION_TEXT, session_id=session_id,
                            message_id=message_id, data={"content": merged}, metadata=metadata,
                        ))
                return

            # Tier 3: work_log_part debounce
            if event == S2CEvent.SESSION_WORK_LOG_PART:
                if isinstance(data, dict):
                    step_id = data.get("step_id", "unknown")
                    existing = wl_buffers.get(step_id, {"text": ""})
                    existing["text"] = existing.get("text", "") + (data.get("text_delta", "") or "")
                    if data.get("status"):
                        existing["status"] = data["status"]
                    wl_buffers[step_id] = existing
                    old_timer = wl_timers.pop(step_id, None)
                    if old_timer:
                        old_timer.cancel()
                    loop = asyncio.get_running_loop()
                    wl_timers[step_id] = loop.call_later(3.0, flush_wl, step_id)
                return

            # Tier 2: immediate events
            if event in IMMEDIATE_EVENTS:
                nonlocal received_agent_response
                queue.put_nowait(ChatEvent(
                    type=event, session_id=session_id,
                    message_id=message_id, data=data, metadata=metadata,
                ))
                # Track substantive agent responses
                if event in (
                    S2CEvent.SESSION_TEXT, S2CEvent.SESSION_FORM_TO_USER,
                    S2CEvent.SESSION_ASK_FOR_LOCATION, S2CEvent.SESSION_TASK_READY,
                    S2CEvent.SESSION_TASK_FINISHED, S2CEvent.SESSION_INTERACTIVE_AUTH_CONFIRMATION,
                    S2CEvent.SESSION_THREE_WAY_CALL, S2CEvent.SESSION_REWARD,
                ):
                    received_agent_response = True
                # Terminal conditions — only after agent has spoken
                if event == S2CEvent.SESSION_INPUT_STATE and isinstance(data, dict):
                    if data.get("content") == "waiting_input" and received_agent_response:
                        done = True
                        queue.put_nowait(None)
                if event == S2CEvent.SESSION_STATE and isinstance(data, dict):
                    state = data.get("content", "")
                    if state in ("task_finished", "task_cancelled", "task_stale"):
                        done = True
                        queue.put_nowait(None)

        remove_handler = self._sio.add_event_handler(handler)

        try:
            while not done:
                evt = await queue.get()
                if evt is None:
                    break
                yield evt
            while not queue.empty():
                evt = queue.get_nowait()
                if evt is not None:
                    yield evt
        finally:
            for t in wl_timers.values():
                t.cancel()
            remove_handler()

    def send_form_response(self, session_id: str, message_id: str, form_data: dict[str, Any]) -> None:
        """Production handler reads payload.data.content as form key-value pairs."""
        self._sio.emit(C2SEvent.SESSION_FORM_TO_USER, {"content": form_data}, session_id, message_id)

    def send_auth_confirmation(self, session_id: str, message_id: str, data: dict[str, Any]) -> None:
        """Production handler reads payload.data.content as confirmation data."""
        self._sio.emit(C2SEvent.SESSION_INTERACTIVE_AUTH_CONFIRMATION, {"content": data}, session_id, message_id)

    def send_location_response(self, session_id: str, message_id: str, latitude: str, longitude: str) -> None:
        """Production handler reads payload.data.content as {latitude, longitude}."""
        self._sio.emit(C2SEvent.SESSION_ASK_FOR_LOCATION, {"content": {"latitude": latitude, "longitude": longitude}}, session_id, message_id)

    def send_location_selection(self, session_id: str, message_id: str, places: list[dict[str, Any]]) -> None:
        """Production handler reads payload.data.list as place objects."""
        self._sio.emit(C2SEvent.SESSION_LOCATION_SELECTION, {"list": places}, session_id, message_id)
