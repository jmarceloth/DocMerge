"""
Letterhead Router - Manage default letterhead in configurable persistent storage
with unique temporary files, atomic replacement, and guaranteed cleanup.
"""

import os
import tempfile
import threading
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse

from app.database import User, get_letterhead_path
from app.auth import get_current_user, require_api_write_access
from app.services.pdf_utils import read_and_validate_pdf_upload

router = APIRouter(prefix="/api/letterhead", tags=["letterhead"])
_letterhead_replace_lock = threading.Lock()


def save_letterhead_atomically(content: bytes, target_path: Path) -> None:
    """
    Grava o timbrado padrão usando um arquivo temporário exclusivo no mesmo diretório,
    realiza substituição atômica via os.replace e garante limpeza do arquivo temporário
    em qualquer cenário de falha ou concorrência.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=str(target_path.parent),
            prefix=f".{target_path.stem}_",
            suffix=".pdf.tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = tmp_file.name
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())

        with _letterhead_replace_lock:
            os.replace(tmp_path, target_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@router.post("/")
async def upload_default_letterhead(
    file: UploadFile = File(...),
    current_user: User = Depends(require_api_write_access),
):
    """Upload, validate and atomically save default letterhead"""
    content, pages = await read_and_validate_pdf_upload(file, label="Timbrado Padrão")

    target_path = get_letterhead_path()
    save_letterhead_atomically(content, target_path)

    return JSONResponse(
        content={
            "message": "Timbrado padrão atualizado com sucesso",
            "filename": file.filename,
            "size": len(content),
            "pages": pages,
        }
    )


@router.get("/")
async def get_default_letterhead_info(
    current_user: User = Depends(get_current_user),
):
    """Check if default letterhead exists"""
    target_path = get_letterhead_path()
    if target_path.exists():
        file_size = target_path.stat().st_size
        return JSONResponse(
            content={
                "exists": True,
                "size": file_size,
                "path": "default_letterhead.pdf",
            }
        )
    return JSONResponse(content={"exists": False})


@router.get("/download")
async def download_default_letterhead(
    current_user: User = Depends(get_current_user),
):
    """Download the default letterhead"""
    target_path = get_letterhead_path()
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Timbrado padrão não encontrado")

    return FileResponse(
        str(target_path),
        media_type="application/pdf",
        filename="timbrado_padrao.pdf",
    )


@router.delete("/")
async def delete_default_letterhead(
    current_user: User = Depends(require_api_write_access),
):
    """Delete default letterhead"""
    target_path = get_letterhead_path()
    with _letterhead_replace_lock:
        if target_path.exists():
            target_path.unlink()
            return JSONResponse(content={"message": "Timbrado padrão removido"})
    raise HTTPException(status_code=404, detail="Timbrado padrão não encontrado")
