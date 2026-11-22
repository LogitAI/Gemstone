"""
Telling a failed connection from other failures (SPEC S3.2, #190).

What urllib3 and requests write when the request never reached a server: name resolution failed, the
network is down, nothing accepted the connection, or the connection attempt timed out. "Max retries
exceeded" alone is not one of them: it also ends a run of 5xx answers.
"""

NO_CONNECTION = (
    "NameResolutionError", "Failed to resolve", "nodename nor servname", "Name or service not known",
    "Temporary failure in name resolution", "getaddrinfo failed", "No address associated with hostname",
    "NewConnectionError", "Failed to establish a new connection", "Network is unreachable",
    "Connection refused", "ConnectTimeoutError", "No route to host",
)


def is_no_connection(text: str) -> bool:
    """ Whether `text` (an error message or a tool result) says that no connection could be made. """
    return any(marker in text for marker in NO_CONNECTION)
