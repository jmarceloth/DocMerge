"""
DocMerge - FastAPI Application
Professional PDF Tools: Merge, Combine, Optimize
"""
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import os

# Import routers
from app.routers import merge, combine, optimize, letterhead

app = FastAPI(
    title="DocMerge",
    description="Professional PDF manipulation tools",
    version="2.0.0"
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure properly in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files and templates (optional)
if os.path.exists("app/static"):
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Include routers
app.include_router(merge.router, prefix="/api/merge", tags=["merge"])
app.include_router(combine.router, prefix="/api/combine", tags=["combine"])
app.include_router(optimize.router, prefix="/api/optimize", tags=["optimize"])
app.include_router(letterhead.router)

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Main page"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "version": "2.0.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
