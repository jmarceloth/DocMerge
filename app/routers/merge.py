"""
Merge Router - Apply letterhead to PDF
"""

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import StreamingResponse
import io

from app.database import User, get_letterhead_path
from app.auth import require_api_write_access
from app.services.pdf_utils import (
    PDFService,
    read_and_validate_pdf_upload,
    validate_pdf_bytes,
    get_max_total_upload_bytes,
)

router = APIRouter()


@router.post("/")
async def merge_with_letterhead(
    content: UploadFile = File(..., description="Content PDF"),
    letterhead: UploadFile = File(None, description="Letterhead PDF (optional, uses default if not provided)"),
    scale: float = Form(0.85, description="Scale factor (0.01-1.0)"),
    current_user: User = Depends(require_api_write_access),
):
    """Merge content PDF with letterhead background"""
    content_bytes, _ = await read_and_validate_pdf_upload(content, label="PDF de Conteúdo")

    if letterhead and (letterhead.filename or "").strip():
        letterhead_bytes, _ = await read_and_validate_pdf_upload(letterhead, label="PDF Timbrado")
        max_total = get_max_total_upload_bytes()
        if len(content_bytes) + len(letterhead_bytes) > max_total:
            raise HTTPException(
                status_code=413,
                detail=f"O tamanho combinado dos arquivos excede o limite de {max_total / (1024 * 1024):.1f} MB.",
            )
    else:
        default_path = get_letterhead_path()
        if not default_path.exists():
            raise HTTPException(
                status_code=400,
                detail="Nenhum timbrado fornecido e não existe timbrado padrão. Por favor, selecione um arquivo ou configure o timbrado padrão.",
            )
        letterhead_bytes = default_path.read_bytes()
        validate_pdf_bytes(letterhead_bytes, label="Timbrado Padrão")

    if not (0.01 <= scale <= 1.0):
        scale = 0.85

    result_bytes = await PDFService.merge_with_letterhead(
        content_bytes,
        letterhead_bytes,
        scale,
    )

    return StreamingResponse(
        io.BytesIO(result_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=mesclado.pdf",
        },
    )
