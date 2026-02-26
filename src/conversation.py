"""
Per-session conversation history and chat utilities.
"""

from typing import List, Dict, Any, Tuple

# Maximum number of user/assistant pairs to keep per session
MAX_HISTORY_PAIRS = 5

# Per-session conversation history keyed by session ID cookie value.
# Each entry is a list of {"role": "user"|"assistant", "content": str}.
_SESSIONS: dict[str, list] = {}

STREAMING_ALLOWED = True


def get_conversation_history(session_id: str) -> List[Dict[str, Any]]:
    """Return a copy of the conversation history for the given session."""
    return list(_SESSIONS.get(session_id, []))


def update_conversation_history(
    session_id: str, query: str | None, response: str | None, clear: bool = False
):
    """Append a user/assistant pair to the session history, or clear it."""
    if clear:
        _SESSIONS.pop(session_id, None)
        return

    history = _SESSIONS.setdefault(session_id, [])
    history.append({"role": "user", "content": query})
    history.append({"role": "assistant", "content": response})

    if len(history) > MAX_HISTORY_PAIRS * 2:
        _SESSIONS[session_id] = history[-MAX_HISTORY_PAIRS * 2 :]


def get_chat_log(conversation_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Format raw conversation history into user/assistant display pairs."""
    chat_log = []
    for i in range(0, len(conversation_history), 2):
        if i + 1 < len(conversation_history):
            chat_log.append(
                {
                    "user": conversation_history[i]["content"],
                    "assistant": conversation_history[i + 1]["content"],
                }
            )
    return chat_log


def check_streaming_status(current_route: str) -> Tuple[bool, str]:
    redirect = False
    new_route = current_route

    if STREAMING_ALLOWED and current_route == "/":
        redirect = True
        new_route = "/streaming"
        return redirect, new_route
    if not STREAMING_ALLOWED and current_route == "/streaming":
        redirect = True
        new_route = "/"
        return redirect, new_route

    return redirect, new_route


def markdown_to_html(text: str) -> str:
    if not text:
        return ""
    return text.replace("\n", "<br>")
