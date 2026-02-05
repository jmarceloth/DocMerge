"""
Letterhead Router - Manage default letterhead
"""
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse
import os

router = APIRouter(prefix="/api/letterhead", tags=["letterhead"])

UPLOADS_DIR = "app/uploads"
DEFAULT_LETTERHEAD_PATH = os.path.join(UPLOADS_DIR, "default_letterhead.pdf")

@router.post("/")
async def upload_default_letterhead(file: UploadFile = File(...)):
    """Upload and save default letterhead"""
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    
    # Ensure uploads directory exists
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    
    # Save file
    content = await file.read()
    with open(DEFAULT_LETTERHEAD_PATH, 'wb') as f:
        f.write(content)
    
    return JSONResponse(content={
        "message": "Timbrado padrão atualizado com sucesso",
        "filename": file.filename,
        "size": len(content)
    })

@router.get("/")
async def get_default_letterhead_info():
    """Check if default letterhead exists"""
    if os.path.exists(DEFAULT_LETTERHEAD_PATH):
        file_size = os.path.getsize(DEFAULT_LETTERHEAD_PATH)
        return JSONResponse(content={
            "exists": True,
            "size": file_size,
            "path": "default_letterhead.pdf"
        })
    else:
        return JSONResponse(content={
            "exists": False
        })

@router.get("/download")
async def download_default_letterhead():
    """Download the default letterhead"""
    if not os.path.exists(DEFAULT_LETTERHEAD_PATH):
        raise HTTPException(status_code=404, detail="Timbrado padrão não encontrado")
    
    return FileResponse(
        DEFAULT_LETTERHEAD_PATH,
        media_type="application/pdf",
        filename="timbrado_padrao.pdf"
    )

@router.delete("/")
async def delete_default_letterhead():
    """Delete default letterhead"""
    if os.path.exists(DEFAULT_LETTERHEAD_PATH):
        os.remove(DEFAULT_LETTERHEAD_PATH)
        return JSONResponse(content={"message": "Timbrado padrão removido"})
    else:
        raise HTTPException(status_code=404, detail="Timbrado padrão não encontrado")
