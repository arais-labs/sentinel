from fastapi import APIRouter

from app.config import build_identity

router = APIRouter()


@router.get("/api/v1/version")
async def version() -> dict[str, str | None]:
    """Identify the running build: the released `version` plus the `commit` and
    `channel` the payload was built from (all from the payload manifest in
    production; `version` falls back to the root VERSION file when run from
    source). See app.config for resolution details.
    """
    return build_identity()
