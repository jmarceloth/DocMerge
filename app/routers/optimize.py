"""
Optimize Router - Compress and optimize PDFs
"""

from fastapi import APIRouter, UploadFile, File, Form, Depends
from fastapi.responses import StreamingResponse
import io

from app.database import User
from app.auth import require_api_write_access
from app.services.pdf_utils import PDFService, read_and_validate_pdf_upload

router = APIRouter()


@router.post("/")
async def optimize_pdf(
    file: UploadFile = File(..., description="PDF to optimize"),
    quality: int = Form(80, description="JPEG quality (60-95)"),
    current_user: User = Depends(require_api_write_access),
):
    """Optimize PDF by compressing images"""
    if not (60 <= quality <= 95):
        quality = 80

    pdf_bytes, _ = await read_and_validate_pdf_upload(file, label="PDF para otimizar")
    result_bytes = await PDFService.optimize_pdf(pdf_bytes, quality)

    return StreamingResponse(
        io.BytesIO(result_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=otimizado.pdf",
        },
    )
