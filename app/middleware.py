"""
Middleware para proteção estrita de rotas e verificação de autorização per-request no SQLite
DocMerge
"""

from urllib.parse import quote
from fastapi import Request
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.database import SessionLocal
from app.auth import get_session_from_cookie, SESSION_COOKIE_NAME


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware que valida em cada requisição protegida se a sessão e o usuário
    estão ativos e autorizados no banco de dados SQLite.
    """

    EXACT_PUBLIC_ROUTES = {
        "/login",
        "/auth/google",
        "/auth/callback",
        "/health",
        "/healthz",
    }

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 1. Permitir apenas rotas públicas estritamente necessárias ao login e healthcheck
        if path in self.EXACT_PUBLIC_ROUTES or path.startswith("/static/"):
            return await call_next(request)

        # 2. Consultar banco de dados para verificar sessão e autorização em tempo real
        db = SessionLocal()
        try:
            user, session = get_session_from_cookie(request, db)

            # Caso usuário bloqueado com sessão existente
            if user and not user.is_active:
                if path.startswith("/api/"):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Conta desativada ou acesso revogado."},
                    )
                response = RedirectResponse(url="/login?erro=bloqueado", status_code=302)
                response.delete_cookie(SESSION_COOKIE_NAME, path="/")
                return response

            # Caso não autenticado ou sessão expirada
            if not user or not session:
                if path.startswith("/api/"):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Não autenticado ou acesso revogado."},
                    )

                redirect_target = f"/login?next={quote(path)}" if path != "/" else "/login"
                response = RedirectResponse(url=redirect_target, status_code=302)
                response.delete_cookie(SESSION_COOKIE_NAME, path="/")
                return response

            # 3. Anexar dados autenticados e CSRF ao estado da requisição
            request.state.user = user
            request.state.session = session
            request.state.csrf_token = session.csrf_token

            # 4. Verificação estrita de rotas administrativas (/admin/*)
            if path == "/admin" or path.startswith("/admin/") or path.startswith("/api/admin/"):
                if user.role != "admin":
                    if path.startswith("/api/"):
                        return JSONResponse(
                            status_code=403,
                            content={"detail": "Acesso negado: privilégio de administrador necessário."},
                        )
                    return HTMLResponse(
                        status_code=403,
                        content="""
                        <!DOCTYPE html>
                        <html lang="pt-BR">
                        <head>
                            <meta charset="UTF-8">
                            <title>403 - Acesso Proibido - DocMerge</title>
                            <style>
                                body { background: #0B0F17; color: #FFFFFF; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                                .card { background: #151C28; border: 1px solid rgba(255, 199, 0, 0.35); border-radius: 12px; padding: 2.5rem; text-align: center; max-width: 480px; }
                                h1 { color: #FFC700; margin-top: 0; }
                                a { color: #FFC700; text-decoration: none; font-weight: bold; }
                                a:hover { text-decoration: underline; }
                            </style>
                        </head>
                        <body>
                            <div class="card">
                                <h1>⛔ 403 - Acesso Negado</h1>
                                <p>Esta seção é restrita exclusivamente a administradores do sistema.</p>
                                <p><a href="/">← Retornar ao DocMerge</a></p>
                            </div>
                        </body>
                        </html>
                        """,
                    )

            # 5. Prosseguir com a requisição protegida
            return await call_next(request)

        finally:
            db.close()
