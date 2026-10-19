"""
DocMerge - FastAPI Application
Professional PDF Tools: Merge, Combine, Optimize (IELECTRIC Standard)
"""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.routers import merge, combine, optimize, letterhead, auth, admin
from app.database import init_db, User
from app.middleware import AuthMiddleware
from app.auth import SECRET_KEY, BASE_URL, IS_PRODUCTION, require_login


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicialização segura do banco de dados SQLite e diretórios persistentes no startup."""
    init_db()
    yield


app = FastAPI(
    title="IELECTRIC DocMerge",
    description="Ferramentas profissionais para manipulação e padronização de PDFs",
    version="2.1.0",
    lifespan=lifespan,
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)

# 1. Configurar cabeçalhos de proxy somente para proxies confiáveis
trusted_proxies_env = os.getenv("TRUSTED_PROXIES", "127.0.0.1,::1")
trusted_proxies = [p.strip() for p in trusted_proxies_env.split(",") if p.strip()]
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=trusted_proxies)

# 2. Configuração restrita de CORS (nunca '*' com credenciais)
cors_origins_env = os.getenv("CORS_ORIGINS", "")
if cors_origins_env.strip():
    allowed_origins = [
        o.strip().rstrip("/")
        for o in cors_origins_env.split(",")
        if o.strip() and o.strip() != "*"
    ]
else:
    allowed_origins = [BASE_URL]
    if not IS_PRODUCTION:
        for dev_origin in ("http://localhost:8000", "http://127.0.0.1:8000"):
            if dev_origin not in allowed_origins:
                allowed_origins.append(dev_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Accept"],
)

# 3. Middleware de autenticação e proteção de rotas per-request no SQLite
app.add_middleware(AuthMiddleware)

# 4. SessionMiddleware exclusivo para handshake temporário do Google OIDC (state/nonce)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="docmerge_oauth_session",
    max_age=600,
    same_site="lax",
    https_only=IS_PRODUCTION,
)

if os.path.exists("app/static"):
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Registro de Roteadores
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(merge.router, prefix="/api/merge", tags=["merge"])
app.include_router(combine.router, prefix="/api/combine", tags=["combine"])
app.include_router(optimize.router, prefix="/api/optimize", tags=["optimize"])
app.include_router(letterhead.router)


@app.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    current_user: User = Depends(require_login),
):
    """Interface principal protegida por sessão autenticada no servidor."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "user": current_user,
            "csrf_token": getattr(request.state, "csrf_token", ""),
        },
    )


@app.get("/health")
@app.get("/healthz")
async def health_check():
    """Endpoint público de verificação de saúde (sem exposição de dados sensíveis)."""
    return {"status": "healthy", "version": "2.1.0"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
