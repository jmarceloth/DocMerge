"""
Merge Router - Apply letterhead to PDF
"""
from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import StreamingResponse
import io

from app.services.pdf_utils import PDFService

router = APIRouter()

@router.post("/")
async def merge_with_letterhead(
    content: UploadFile = File(..., description="Content PDF"),
    letterhead: UploadFile = File(..., description="Letterhead PDF"),
    scale: float = Form(0.85, description="Scale factor (0.01-1.0)")
):
    """Merge content PDF with letterhead background"""
    
    # Read files
    content_bytes = await content.read()
    letterhead_bytes = await letterhead.read()
    
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
