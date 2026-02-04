"""
Optimize Router - Compress and optimize PDFs
"""
from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import StreamingResponse
import io

from app.services.pdf_utils import PDFService

router = APIRouter()

@router.post("/")
async def optimize_pdf(
    file: UploadFile = File(..., description="PDF to optimize"),
    quality: int = Form(80, description="JPEG quality (60-95)")
):
    """Optimize PDF by compressing images"""
    
    # Validate quality
    if not (60 <= quality <= 95):
        quality = 80
    
    # Read file
    pdf_bytes = await file.read()
    
    # Optimize
    result_bytes = await PDFService.optimize_pdf(pdf_bytes, quality)
    
    # Return
    return StreamingResponse(
        io.BytesIO(result_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=otimizado.pdf"
        }
    )
