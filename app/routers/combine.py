"""
Combine Router - Combine multiple PDFs into one
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import StreamingResponse
from typing import List
import io

from app.database import User
from app.auth import require_api_write_access
from app.services.pdf_utils import (
    PDFService,
    read_and_validate_pdf_upload,
    get_max_combine_files,
    get_max_total_upload_bytes,
    get_max_pdf_pages,
)

router = APIRouter()


@router.post("/")
async def combine_pdfs(
    files: List[UploadFile] = File(..., description="PDFs to combine"),
    current_user: User = Depends(require_api_write_access),
):
    """Combine multiple PDF files into one"""
    if len(files) < 2:
        raise HTTPException(status_code=400, detail="Selecione pelo menos 2 arquivos PDF para combinar.")

    max_files = get_max_combine_files()
    if len(files) > max_files:
        raise HTTPException(
            status_code=400,
            detail=f"Quantidade máxima de arquivos excedida (máximo permitido: {max_files} arquivos).",
        )

    max_total_bytes = get_max_total_upload_bytes()
    max_total_pages = get_max_pdf_pages()

    pdf_bytes_list = []
    total_bytes = 0
    total_pages = 0

    for idx, file in enumerate(files, start=1):
        pdf_bytes, pages = await read_and_validate_pdf_upload(file, label=f"Arquivo #{idx}")
        total_bytes += len(pdf_bytes)
        if total_bytes > max_total_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"O tamanho total dos arquivos excede o limite de {max_total_bytes / (1024 * 1024):.1f} MB.",
            )
        total_pages += pages
        if total_pages > max_total_pages:
            raise HTTPException(
                status_code=400,
                detail=f"O total de páginas combinadas ({total_pages}) excede o limite de {max_total_pages} páginas.",
            )
        pdf_bytes_list.append(pdf_bytes)

    result_bytes = await PDFService.combine_pdfs(pdf_bytes_list)

    return StreamingResponse(
        io.BytesIO(result_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=combinado.pdf",
        },
    )
