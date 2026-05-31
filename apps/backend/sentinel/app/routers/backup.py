"""Instance-scoped selective backup & restore of domain items."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_request_instance_runtime_context
from app.middleware.auth import TokenPayload, require_auth
from app.services.instance_runtime_context import instance_runtime_context_registry
from app.schemas.backup import (
    BackupInfoResponse,
    ExportRequest,
    ImportRequest,
    ImportResponse,
    InspectRequest,
    ItemInfo,
    ItemsResponse,
)
from app.services.backup import (
    BackupCompatibilityError,
    BackupFormatError,
    BackupPassphraseError,
    available_items,
    export_backup,
    import_backup,
    inspect_backup,
)

router = APIRouter()


def _decode_blob(data: str) -> bytes:
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file is not valid base64.",
        ) from exc


@router.get("/items", response_model=ItemsResponse)
async def list_items(
    user: TokenPayload = Depends(require_auth),
) -> ItemsResponse:
    _ = user
    return ItemsResponse(items=[ItemInfo(**i) for i in available_items()])


@router.post("/export")
async def export_archive(
    payload: ExportRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    _ = user
    if not payload.passphrase:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="A passphrase is required."
        )
    if not payload.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select at least one item to back up.",
        )
    instance_name = request.path_params.get("instance_name")
    try:
        blob = await export_backup(
            db,
            instance_name=instance_name,
            items=payload.items,
            passphrase=payload.passphrase,
        )
    except BackupPassphraseError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"sentinel-{instance_name or 'instance'}-{stamp}.sntl"
    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/inspect", response_model=BackupInfoResponse)
async def inspect_uploaded_backup(
    payload: InspectRequest,
    user: TokenPayload = Depends(require_auth),
) -> BackupInfoResponse:
    _ = user
    blob = _decode_blob(payload.data)
    try:
        info = inspect_backup(blob, payload.passphrase)
    except BackupPassphraseError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except BackupFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return BackupInfoResponse(**info)


@router.post("/import", response_model=ImportResponse)
async def import_archive(
    payload: ImportRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> ImportResponse:
    blob = _decode_blob(payload.data)
    try:
        summary = await import_backup(
            db,
            blob,
            payload.passphrase,
            items=payload.items,
            owner_user_id=user.sub,
        )
    except BackupPassphraseError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (BackupFormatError, BackupCompatibilityError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    if "modules" in summary.items:
        try:
            context = get_request_instance_runtime_context(request)
        except RuntimeError:
            context = None
        if context is not None:
            await instance_runtime_context_registry.rebuild_context(
                app_state=request.app.state,
                context=context,
            )

    return ImportResponse(**summary.as_dict())
