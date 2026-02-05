"""
Merge Router - Apply letterhead to PDF
"""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
import io
import os

from app.services.pdf_utils import PDFService

router = APIRouter()

DEFAULT_LETTERHEAD_PATH = "app/uploads/default_letterhead.pdf"

@router.post("/")
async def merge_with_letterhead(
    content: UploadFile = File(..., description="Content PDF"),
    letterhead: UploadFile = File(None, description="Letterhead PDF (optional, uses default if not provided)"),
    scale: float = Form(0.85, description="Scale factor (0.01-1.0)")
):
    """Merge content PDF with letterhead background"""
    
    # Read content file
    content_bytes = await content.read()
    
    # Get letterhead bytes - from upload or default
    if letterhead:
        letterhead_bytes = await letterhead.read()
    else:
        # Use default letterhead
        if not os.path.exists(DEFAULT_LETTERHEAD_PATH):
            raise HTTPException(
                status_code=400,
                detail="Nenhum timbrado fornecido e não existe timbrado padrão. Por favor, selecione um arquivo ou configure o timbrado padrão."
            )
        with open(DEFAULT_LETTERHEAD_PATH, 'rb') as f:
            letterhead_bytes = f.read()
    
    # Validate scale
    if not (0.01 <= scale <= 1.0):
        scale = 0.85
    
    # Merge PDFs
    result_bytes = await PDFService.merge_with_letterhead(
        content_bytes,
        letterhead_bytes,
        scale
    )
    
   # Return as downloadable file
    return StreamingResponse(
        io.BytesIO(result_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=mesclado.pdf"
        }
    )
