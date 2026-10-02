"""
Per-session tool-result cache. Idea and first implementation from PR #53 by @Mir47-47.

The server keeps every tool result of a session in `Session.tool_call_caches` (call id -> result).
The client's history holds only the placeholder `<cached_result:<call id>>`, so a later turn reads
the result back with the `get_cache_data` tool instead of calling the original tool again.
"""
from json import dumps


def get_cache_data(tool_call_cache_id: str, tool_call_caches: dict | None = None) -> str:
    """ Return the cached result of an earlier tool call. """
    if tool_call_caches and tool_call_cache_id in tool_call_caches:
        data = tool_call_caches[tool_call_cache_id]
        return data if isinstance(data, str) else dumps(data, default=str, ensure_ascii=False)
    return f"Cache '{tool_call_cache_id}' not found"
