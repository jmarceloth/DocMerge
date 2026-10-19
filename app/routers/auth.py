"""
Rotas de Autenticação Google OpenID Connect (OIDC) e Encerramento de Sessão
DocMerge
"""

import secrets
from urllib.parse import quote, unquote
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db, User
from app.auth import (
    oauth,
    GOOGLE_REDIRECT_URI,
    IS_PRODUCTION,
    SESSION_COOKIE_NAME,
    SESSION_EXPIRE_HOURS,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    register_oauth_handshake_state,
    consume_oauth_handshake_state,
    create_user_session,
    revoke_session,
    get_session_from_cookie,
    is_google_native_or_workspace,
    validate_csrf_token,
    validate_oidc_claims,
)
from app.services.user_service import (
    vincular_google_sub_condicional,
    registrar_pendencia_terceiro_condicional,
)

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    erro: str = None,
    sucesso: str = None,
    next: str = None,
    email: str = None,
    db: Session = Depends(get_db),
):
    """Renderiza a página de login com o botão 'Entrar com o Google' e mensagens contextuais."""
    user, session = get_session_from_cookie(request, db)
    if user and session and user.is_active:
        return RedirectResponse(url="/", status_code=302)

    mensagens_erro = {
        "nao_autorizado": f"Acesso não autorizado. A conta Google '{email or ''}' não possui permissão para acessar este sistema. Solicite o cadastro prévio ao proprietário.",
        "bloqueado": "Acesso suspenso. Esta conta foi desativada pelo administrador do sistema.",
        "terceiro_pendente": f"Conta Google com e-mail de provedor terceiro '{email or ''}' registrada. Por segurança, o vínculo inicial requer confirmação prévia pelo administrador antes da concessão de acesso.",
        "conflito_identidade": "Conflito de identidade: este e-mail já está vinculado permanentemente a outro identificador Google.",
        "conflito_vinculo": "Não foi possível concluir o vínculo: esta conta Google já está associada a outro cadastro.",
        "sub_ja_vinculado": "Esta conta Google já está vinculada permanentemente a outro usuário do sistema.",
        "candidato_conflito": "Já existe uma análise de identidade em andamento para este e-mail com outra conta Google. Contate o administrador.",
        "conta_legada_nao_vinculada": "Conta local legada desativada. Solicite a migração e autorização para sua conta Google.",
        "email_nao_verificado": "A conta Google utilizada não possui endereço de e-mail verificado pela Google.",
        "oauth_falha": "Falha na validação segura OIDC com a Google (state, assinatura ou claims inválidos). Tente novamente.",
        "csrf": "Sessão ou token de segurança inválido. Tente novamente.",
        "configuracao": "Serviço de autenticação Google não configurado no servidor (credenciais ausentes).",
    }

    mensagens_sucesso = {
        "logout": "Você encerrou a sessão com sucesso.",
    }

    mensagem_erro_texto = mensagens_erro.get(erro, unquote(erro) if erro else None)
    mensagem_sucesso_texto = mensagens_sucesso.get(sucesso, unquote(sucesso) if sucesso else None)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "erro": mensagem_erro_texto,
            "sucesso": mensagem_sucesso_texto,
            "next": next or "/",
        },
    )


@router.get("/auth/google")
async def auth_google(request: Request, next: str = "/", db: Session = Depends(get_db)):
    """
    Inicia o fluxo de autenticação OpenID Connect com state, nonce e browser_bind
    imprevisíveis, registrando o state temporariamente no servidor para consumo único.
    """
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return RedirectResponse(url="/login?erro=configuracao", status_code=302)

    safe_next = next if next and next.startswith("/") and not next.startswith("//") else "/"
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    browser_bind = secrets.token_urlsafe(32)

    register_oauth_handshake_state(
        db=db,
        state=state,
        browser_bind=browser_bind,
        nonce=nonce,
        next_url=safe_next,
    )

    request.session["next_url"] = safe_next
    request.session["oauth_expected_state"] = state
    request.session["oauth_expected_nonce"] = nonce
    request.session["oauth_browser_bind"] = browser_bind

    return await oauth.google.authorize_redirect(
        request,
        GOOGLE_REDIRECT_URI,
        state=state,
        nonce=nonce,
        prompt="select_account",
    )


@router.get("/auth/callback")
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    """
    Recebe o retorno OIDC do Google, consome atomicamente o state no banco do servidor
    (garantindo uso único real mesmo se o cookie do handshake for reapresentado) ANTES
    da troca de código OAuth, e valida assinatura RS256, iss, aud, exp obrigatório,
    nonce obrigatório, email_verified e whitelist.
    """
    has_session = "session" in request.scope
    session_state = request.session.pop("oauth_expected_state", None) if has_session else None
    session_nonce = request.session.pop("oauth_expected_nonce", None) if has_session else None
    browser_bind = request.session.pop("oauth_browser_bind", None) if has_session else None
    request.session.pop("next_url", None) if has_session else None

    query_state = request.query_params.get("state")

    # Rejeita imediatamente se state, nonce ou vínculo de navegador estiverem ausentes ou divergentes
    if (
        not query_state
        or not session_state
        or not session_nonce
        or not browser_bind
        or not secrets.compare_digest(str(query_state), str(session_state))
    ):
        if has_session:
            request.session.clear()
        return RedirectResponse(url="/login?erro=oauth_falha", status_code=302)

    # Consumo atômico de uso único no banco do servidor ANTES de qualquer troca de código OAuth
    consumed = consume_oauth_handshake_state(
        db=db,
        state=str(query_state),
        browser_bind=str(browser_bind),
        expected_nonce=str(session_nonce),
    )
    if consumed is None:
        if has_session:
            request.session.clear()
        return RedirectResponse(url="/login?erro=oauth_falha", status_code=302)

    expected_nonce, next_url = consumed

    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        if has_session:
            request.session.clear()
        print(f"[OAuth Callback Error] {e}")
        return RedirectResponse(url="/login?erro=oauth_falha", status_code=302)
    finally:
        if has_session:
            for k in list(request.session.keys()):
                if k.startswith("_state_google_") or k in (
                    "oauth_expected_state",
                    "oauth_expected_nonce",
                    "oauth_browser_bind",
                ):
                    request.session.pop(k, None)

    user_info = token.get("userinfo")
    if not user_info and token.get("id_token"):
        try:
            user_info = await oauth.google.parse_id_token(token, nonce=expected_nonce)
        except Exception as e:
            print(f"[OIDC ID Token Parse Error] {e}")
            return RedirectResponse(url="/login?erro=oauth_falha", status_code=302)

    if not user_info:
        return RedirectResponse(url="/login?erro=oauth_falha", status_code=302)

    # Exige validação de nonce vinculada ao handshake
    token_nonce = user_info.get("nonce")
    if not token_nonce or not isinstance(token_nonce, str) or not secrets.compare_digest(token_nonce, expected_nonce):
        return RedirectResponse(url="/login?erro=oauth_falha", status_code=302)

    # Se os claims vieram com campos completos de JWT (iss, aud, exp), valida-os estritamente
    if any(k in user_info for k in ("iss", "aud", "exp")):
        try:
            validate_oidc_claims(dict(user_info), expected_nonce=expected_nonce)
        except Exception as e:
            print(f"[OIDC Claims Validation Error] {e}")
            return RedirectResponse(url="/login?erro=oauth_falha", status_code=302)

    # 1. Validação estrita de e-mail verificado pela Google
    if user_info.get("email_verified") is not True:
        return RedirectResponse(url="/login?erro=email_nao_verificado", status_code=302)

    email = str(user_info.get("email", "")).strip().lower()
    google_sub = str(user_info.get("sub", "")).strip()
    hd = user_info.get("hd")
    name = user_info.get("name")
    picture = user_info.get("picture")

    if not email or not google_sub:
        return RedirectResponse(url="/login?erro=oauth_falha", status_code=302)

    is_native = is_google_native_or_workspace(email, hd)

    # 2. Localização do usuário no banco (sem autorização automática de contas ou domínios)
    user = db.query(User).filter(User.google_sub == google_sub).first()

    if user:
        if not user.is_active:
            return RedirectResponse(url="/login?erro=bloqueado", status_code=302)
        if user.third_party_pending:
            return RedirectResponse(url=f"/login?erro=terceiro_pendente&email={quote(email)}", status_code=302)

        user.name = name or user.name
        user.picture_url = picture or user.picture_url
        user.last_login = datetime.now(timezone.utc)
        db.commit()

    else:
        matches = db.query(User).filter(func.lower(User.email) == email).all()
        if not matches:
            return RedirectResponse(url=f"/login?erro=nao_autorizado&email={quote(email)}", status_code=302)
        if len(matches) > 1:
            return RedirectResponse(url="/login?erro=conflito_identidade", status_code=302)

        user = matches[0]
        if not user.is_active:
            return RedirectResponse(url="/login?erro=bloqueado", status_code=302)

        if user.google_sub is not None and user.google_sub != google_sub:
            return RedirectResponse(url="/login?erro=conflito_identidade", status_code=302)

        if user.auth_type == "legacy_disabled":
            return RedirectResponse(url="/login?erro=conta_legada_nao_vinculada", status_code=302)

        if not is_native:
            sucesso, erro_cand = registrar_pendencia_terceiro_condicional(
                db=db,
                user_id=user.id,
                google_sub=google_sub,
                name=name or user.name,
                picture_url=picture or user.picture_url,
            )
            if not sucesso:
                return RedirectResponse(
                    url=f"/login?erro={erro_cand or 'candidato_conflito'}&email={quote(email)}",
                    status_code=302,
                )
            return RedirectResponse(url=f"/login?erro=terceiro_pendente&email={quote(email)}", status_code=302)

        sucesso, motivo_erro, user_vinculado = vincular_google_sub_condicional(
            db=db,
            user_id=user.id,
            google_sub=google_sub,
            name=name or user.name,
            picture_url=picture or user.picture_url,
        )
        if not sucesso:
            return RedirectResponse(
                url=f"/login?erro={motivo_erro or 'conflito_vinculo'}&email={quote(email)}",
                status_code=302,
            )
        user = user_vinculado

    # 3. Criação de sessão revogável no servidor
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    raw_token, _ = create_user_session(db, user, ip_address=client_ip, user_agent=user_agent)

    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"

    response = RedirectResponse(url=next_url, status_code=302)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        max_age=SESSION_EXPIRE_HOURS * 3600,
        secure=IS_PRODUCTION,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout")
async def logout(
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Encerra a sessão no servidor com validação de token CSRF."""
    validate_csrf_token(request, csrf_token)

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        revoke_session(db, token)

    response = RedirectResponse(url="/login?sucesso=logout", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@router.get("/logout")
async def logout_get():
    """Redireciona requisições GET para evitar logout acidental ou CSRF via links."""
    return RedirectResponse(url="/", status_code=302)
