"""
Combine Router - Combine multiple PDFs into one
"""
from fastapi import APIRouter, UploadFile, File
from fastapi.responses import StreamingResponse
from typing import List
import io

from app.services.pdf_utils import PDFService

router = APIRouter()

@router.post("/")
async def combine_pdfs(
    files: List[UploadFile] = File(..., description="PDFs to combine")
):
    """Combine multiple PDF files into one"""
    
    if len(files) < 2:
        from fastapi import HTTPException
        raise HTTPException(400, "At least 2 files required")
    
    # Read all files
    pdf_bytes_list = []
    for file in files:
        pdf_bytes_list.append(await file.read())
    
    # Combine
    result_bytes = await PDFService.combine_pdfs(pdf_bytes_list)
    
    # Return
    return StreamingResponse(
        io.BytesIO(result_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=combinado.pdf"
        }
    )
