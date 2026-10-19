"""
Rotas administrativas para gestão da lista de pessoas autorizadas e papéis (RBAC)
DocMerge
"""

from urllib.parse import quote
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db, User
from app.auth import require_admin, validate_csrf_token
from app.services.user_service import (
    bloquear_usuario_atomico,
    reativar_usuario_atomico,
    alterar_papel_atomico,
    remover_usuario_atomico,
    confirmar_candidato_terceiro_atomico,
)

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/usuarios", response_class=HTMLResponse)
async def listar_usuarios(
    request: Request,
    sucesso: str = None,
    erro: str = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Exibe painel de controle de usuários autorizados."""
    usuarios = db.query(User).order_by(User.role.desc(), User.created_at.asc()).all()

    return templates.TemplateResponse(
        request=request,
        name="admin_usuarios.html",
        context={
            "user": current_user,
            "usuarios": usuarios,
            "sucesso": sucesso,
            "erro": erro,
            "csrf_token": getattr(request.state, "csrf_token", ""),
        },
    )


@router.post("/usuarios/adicionar")
async def adicionar_usuario(
    request: Request,
    email: str = Form(...),
    role: str = Form("user"),
    csrf_token: str = Form(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Adiciona um novo e-mail à lista de pessoas autorizadas."""
    validate_csrf_token(request, csrf_token)
    email_clean = email.strip().lower()

    if not email_clean or "@" not in email_clean:
        return RedirectResponse(url="/admin/usuarios?erro=E-mail inválido", status_code=303)

    if role not in ("admin", "user"):
        role = "user"

    existente = db.query(User).filter(func.lower(User.email) == email_clean).first()
    if existente:
        return RedirectResponse(
            url=f"/admin/usuarios?erro=O e-mail '{email_clean}' já está cadastrado no sistema.",
            status_code=303,
        )

    novo_usuario = User(
        email=email_clean,
        google_sub=None,
        role=role,
        is_active=True,
        auth_type="google",
        third_party_pending=False,
        created_at=datetime.now(timezone.utc),
    )
    db.add(novo_usuario)
    db.commit()

    return RedirectResponse(
        url=f"/admin/usuarios?sucesso=E-mail '{email_clean}' autorizado com sucesso como {role.upper()}.",
        status_code=303,
    )


@router.post("/usuarios/bloquear")
async def bloquear_usuario(
    request: Request,
    user_id: int = Form(...),
    csrf_token: str = Form(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Bloqueia um usuário e revoga imediatamente todas as suas sessões na mesma transação atômica."""
    validate_csrf_token(request, csrf_token)
    try:
        usuario = bloquear_usuario_atomico(db, user_id)
        return RedirectResponse(
            url=f"/admin/usuarios?sucesso=Usuário '{usuario.email}' bloqueado e sessões encerradas.",
            status_code=303,
        )
    except ValueError as e:
        return RedirectResponse(url=f"/admin/usuarios?erro={quote(str(e))}", status_code=303)


@router.post("/usuarios/reativar")
async def reativar_usuario(
    request: Request,
    user_id: int = Form(...),
    csrf_token: str = Form(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Reativa um usuário bloqueado de forma atômica (exigirá novo login pelo Google)."""
    validate_csrf_token(request, csrf_token)
    try:
        usuario = reativar_usuario_atomico(db, user_id)
        return RedirectResponse(
            url=f"/admin/usuarios?sucesso=Usuário '{usuario.email}' reativado com sucesso.",
            status_code=303,
        )
    except ValueError as e:
        return RedirectResponse(url=f"/admin/usuarios?erro={quote(str(e))}", status_code=303)


@router.post("/usuarios/alterar-papel")
async def alterar_papel(
    request: Request,
    user_id: int = Form(...),
    novo_papel: str = Form(...),
    csrf_token: str = Form(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Altera o papel do usuário entre 'admin' e 'user' com verificação serializada de admin utilizável."""
    validate_csrf_token(request, csrf_token)
    try:
        usuario = alterar_papel_atomico(db, user_id, novo_papel)
        return RedirectResponse(
            url=f"/admin/usuarios?sucesso=Papel do usuário '{usuario.email}' atualizado para {novo_papel.upper()}.",
            status_code=303,
        )
    except ValueError as e:
        return RedirectResponse(url=f"/admin/usuarios?erro={quote(str(e))}", status_code=303)


@router.post("/usuarios/confirmar-terceiro")
async def confirmar_terceiro(
    request: Request,
    user_id: int = Form(...),
    candidate_sub: str = Form(...),
    csrf_token: str = Form(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Confirma administrativamente o vínculo Google garantindo a identidade analisada."""
    validate_csrf_token(request, csrf_token)
    try:
        usuario = confirmar_candidato_terceiro_atomico(db, user_id, candidate_sub)
        return RedirectResponse(
            url=f"/admin/usuarios?sucesso=Identidade Google ({candidate_sub}) para '{usuario.email}' confirmada com sucesso.",
            status_code=303,
        )
    except ValueError as e:
        return RedirectResponse(url=f"/admin/usuarios?erro={quote(str(e))}", status_code=303)


@router.post("/usuarios/remover")
async def remover_usuario(
    request: Request,
    user_id: int = Form(...),
    csrf_token: str = Form(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Remove um usuário e todas as suas sessões na mesma transação atômica."""
    validate_csrf_token(request, csrf_token)
    try:
        email_removido = remover_usuario_atomico(db, user_id)
        return RedirectResponse(
            url=f"/admin/usuarios?sucesso=Usuário '{email_removido}' removido do sistema.",
            status_code=303,
        )
    except ValueError as e:
        return RedirectResponse(url=f"/admin/usuarios?erro={quote(str(e))}", status_code=303)
