"""Hand the "Open in Cove" browser extension to signed-in users as a zip.

Shipped inside the image (vendored from its own repo — see
scripts/vendor-extension.sh), so a deployment with no outbound internet can still
offer it. Nothing here is user-supplied: the archive is built from files on disk.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from server.deps import CurrentUser
from server.extension import ROOT_NAME, build
from server.schemas import ExtensionOut

router = APIRouter(prefix="/api/extension", tags=["extension"])


@router.get("", response_model=ExtensionOut)
def extension_info(user: CurrentUser):
    """Whether this deployment ships the extension, and which version."""
    built = build()
    if built is None:
        return ExtensionOut(available=False, version=None, size_bytes=0, filename="")
    data, _etag, meta = built
    version = meta.get("version") or None
    return ExtensionOut(
        available=True,
        version=version,
        size_bytes=len(data),
        filename=_filename(version),
    )


def _filename(version: "str | None") -> str:
    return f"{ROOT_NAME}-{version}.zip" if version else f"{ROOT_NAME}.zip"


@router.get("/download")
def download_extension(user: CurrentUser, request: Request):
    built = build()
    if built is None:
        raise HTTPException(status_code=404, detail="This deployment doesn't include the browser extension")
    data, etag, meta = built
    quoted = f'"{etag}"'
    if request.headers.get("if-none-match") == quoted:
        return Response(status_code=304, headers={"ETag": quoted})
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(meta.get("version"))}"',
            # The same vendored files always produce the same bytes, so a
            # conditional request can be answered from the browser's copy.
            "ETag": quoted,
            "Cache-Control": "private, max-age=0, must-revalidate",
        },
    )
