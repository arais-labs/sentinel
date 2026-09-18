"""Safe, user-facing connection errors; never display raw transport exceptions."""

import httpx2


class SignInRequired(RuntimeError):
    pass


def http_status(error):
    if isinstance(error, httpx2.HTTPStatusError):
        return error.response.status_code
    for child in getattr(error, "exceptions", []):
        status = http_status(child)
        if status:
            return status
    if error.__cause__ is not None:
        return http_status(error.__cause__)
    return None


def connection_message(error):
    if isinstance(error, SignInRequired) or http_status(error) in {401, 403}:
        return "This server requires sign-in. Connect it from MCP settings."
    if isinstance(error, TimeoutError):
        return "The server did not respond in time. Check its address and try again."
    return "Could not connect. Check the server address and authentication settings."
