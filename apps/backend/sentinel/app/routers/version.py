from pathlib import Path

from fastapi import APIRouter

router = APIRouter()


def _walk_up_for_marker(name: str) -> str | None:
    cursor = Path(__file__).resolve().parent
    for _ in range(8):
        candidate = cursor / name
        if candidate.is_file():
            value = candidate.read_text(encoding="utf-8").strip()
            return value or None
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    return None


@router.get("/api/v1/version")
async def version() -> dict[str, str | None]:
    """Return the commit + channel that the desktop supervisor stamped after
    its last successful bootstrap or update. Source: `.runtime-commit` and
    `.runtime-channel` files written by the supervisor at the source-tree
    root. The supervisor is authoritative; this endpoint is a thin HTTP
    wrapper so the main UI can show a version badge.
    """
    return {
        "commit": _walk_up_for_marker(".runtime-commit"),
        "channel": _walk_up_for_marker(".runtime-channel"),
    }
