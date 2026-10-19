"""
Módulo de Autenticação Google OpenID Connect (OIDC), Sessões Revogáveis,
Handshake State Server-Side de Uso Único e Controle de Acesso (RBAC) - DocMerge
"""

import os
import time
import uuid
import secrets
import hashlib
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Dict, Any
from fastapi import Request, HTTPException, status, Depends, Form
from sqlalchemy.orm import Session
from authlib.integrations.starlette_client import OAuth

from app.database import User, UserSession, OAuthHandshakeState, get_db
from app.services.user_service import is_admin_usable, iniciar_transacao_imediata

FORBIDDEN_DEFAULT_KEYS = {
    "",
    "sua-chave-secreta-super-segura-mude-isso-em-producao-12345",
    "substitua_por_uma_chave_secreta_forte_e_aleatoria",
    "sua-chave-secreta-longa-e-aleatoria-para-producao",
    "changeme",
    "secret",
}

FORBIDDEN_GOOGLE_PLACEHOLDERS = {
    "",
    "seu-google-client-id.apps.googleusercontent.com",
    "seu-google-client-secret",
    "changeme",
}

LOCALHOST_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
ALLOWED_ENVIRONMENTS = {"production", "prod", "development", "dev", "testing", "test"}
ALLOWED_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
SESSION_COOKIE_NAME = "docmerge_session"
OAUTH_STATE_TTL_SECONDS = 600


def validate_runtime_config(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Valida estritamente as variáveis de ambiente de execução.
    - Sem fallback silencioso para modo de desenvolvimento: se ENVIRONMENT estiver ausente,
      assume 'production' por segurança.
    - Em produção (ENVIRONMENT=production): exige SECRET_KEY forte (>= 32 chars),
      BASE_URL HTTPS válida (sem fallback para localhost), GOOGLE_REDIRECT_URI HTTPS válida
      e credenciais GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET reais.
    """
    src = env if env is not None else os.environ

    raw_env = (src.get("ENVIRONMENT") or "production").strip().lower()
    if raw_env not in ALLOWED_ENVIRONMENTS:
        raise ValueError(
            f"Variável ENVIRONMENT inválida ('{raw_env}'). Use 'production', 'development' ou 'testing'."
        )
    is_production = raw_env in ("production", "prod")

    secret_key = (src.get("SECRET_KEY") or "").strip()
    min_secret_len = 32 if is_production else 16
    if not secret_key or secret_key in FORBIDDEN_DEFAULT_KEYS or len(secret_key) < min_secret_len:
        raise ValueError(
            f"A variável de ambiente SECRET_KEY deve ser configurada com uma chave secreta "
            f"exclusiva e forte (mínimo {min_secret_len} caracteres). Não é permitido usar chaves padrão."
        )

    raw_base_url = (src.get("BASE_URL") or "").strip().rstrip("/")
    if is_production:
        if not raw_base_url:
            raise ValueError(
                "Em produção (ENVIRONMENT=production), a variável BASE_URL é obrigatória "
                "(ex: https://docmerge.ielectric.app). Fallback para localhost não é permitido."
            )
        parsed_base = urlparse(raw_base_url)
        hostname = (parsed_base.hostname or "").lower()
        if parsed_base.scheme != "https" or not hostname or hostname in LOCALHOST_HOSTS:
            raise ValueError(
                "Em produção (ENVIRONMENT=production), BASE_URL deve ser uma URL HTTPS pública válida "
                "(não é permitido HTTP ou localhost)."
            )
        base_url = raw_base_url
    else:
        base_url = raw_base_url or "http://localhost:8000"
        parsed_base = urlparse(base_url)
        if parsed_base.scheme not in ("http", "https") or not parsed_base.hostname:
            raise ValueError("BASE_URL inválida.")

    raw_redirect_uri = (src.get("GOOGLE_REDIRECT_URI") or "").strip()
    expected_callback = f"{base_url}/auth/callback"
    if raw_redirect_uri:
        parsed_redirect = urlparse(raw_redirect_uri)
        r_host = (parsed_redirect.hostname or "").lower()
        if is_production and (parsed_redirect.scheme != "https" or not r_host or r_host in LOCALHOST_HOSTS):
            raise ValueError(
                "Em produção (ENVIRONMENT=production), GOOGLE_REDIRECT_URI deve ser uma URL HTTPS pública válida."
            )
        if raw_redirect_uri.rstrip("/") != expected_callback:
            raise ValueError(
                f"GOOGLE_REDIRECT_URI ('{raw_redirect_uri}') deve corresponder a '{expected_callback}'."
            )
        redirect_uri = raw_redirect_uri.rstrip("/")
    else:
        redirect_uri = expected_callback

    google_client_id = (src.get("GOOGLE_CLIENT_ID") or "").strip()
    google_client_secret = (src.get("GOOGLE_CLIENT_SECRET") or "").strip()
    if is_production:
        if (
            google_client_id in FORBIDDEN_GOOGLE_PLACEHOLDERS
            or google_client_secret in FORBIDDEN_GOOGLE_PLACEHOLDERS
        ):
            raise ValueError(
                "Em produção (ENVIRONMENT=production), GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET "
                "são obrigatórios e não podem conter valores de exemplo."
            )

    session_hours_raw = src.get("SESSION_MAX_AGE_HOURS", "8")
    try:
        session_max_age_hours = int(session_hours_raw)
        if session_max_age_hours <= 0:
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise ValueError(
            "A variável de ambiente SESSION_MAX_AGE_HOURS deve ser um número inteiro estritamente positivo (horas)."
        ) from exc

    return {
        "ENVIRONMENT": raw_env,
        "IS_PRODUCTION": is_production,
        "SECRET_KEY": secret_key,
        "BASE_URL": base_url,
        "GOOGLE_REDIRECT_URI": redirect_uri,
        "GOOGLE_CLIENT_ID": google_client_id,
        "GOOGLE_CLIENT_SECRET": google_client_secret,
        "SESSION_MAX_AGE_HOURS": session_max_age_hours,
    }


_cfg = validate_runtime_config()
ENVIRONMENT = _cfg["ENVIRONMENT"]
IS_PRODUCTION = _cfg["IS_PRODUCTION"]
SECRET_KEY = _cfg["SECRET_KEY"]
BASE_URL = _cfg["BASE_URL"]
GOOGLE_REDIRECT_URI = _cfg["GOOGLE_REDIRECT_URI"]
GOOGLE_CLIENT_ID = _cfg["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = _cfg["GOOGLE_CLIENT_SECRET"]
SESSION_MAX_AGE_HOURS = _cfg["SESSION_MAX_AGE_HOURS"]
SESSION_EXPIRE_HOURS = SESSION_MAX_AGE_HOURS


def validate_oidc_claims(
    claims: Dict[str, Any],
    expected_nonce: Optional[str] = None,
    expected_aud: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Valida estritamente no servidor os claims obrigatórios do ID Token Google OIDC:
    - Emissor (iss)
    - Audience (aud)
    - Expiração obrigatória (exp presente e futura)
    - Nonce obrigatório e correspondente à sessão do navegador
    - Identificador imutável (sub) e e-mail presentes
    """
    if not isinstance(claims, dict):
        raise ValueError("Claims OIDC inválidos.")

    iss = claims.get("iss")
    if iss not in ALLOWED_ISSUERS:
        raise ValueError(f"Emissor OIDC inválido: {iss}")

    aud_target = expected_aud or GOOGLE_CLIENT_ID
    aud = claims.get("aud")
    if isinstance(aud, list):
        if aud_target not in aud:
            raise ValueError("Audience (aud) OIDC inválida.")
    elif aud != aud_target:
        raise ValueError("Audience (aud) OIDC inválida.")

    if "exp" not in claims or claims.get("exp") is None:
        raise ValueError("Claim 'exp' (expiração) obrigatório ausente no ID Token.")
    try:
        exp_int = int(claims["exp"])
    except (ValueError, TypeError) as exc:
        raise ValueError("Claim 'exp' inválido no ID Token.") from exc

    if exp_int <= int(time.time()):
        raise ValueError("ID Token OIDC expirado.")

    token_nonce = claims.get("nonce")
    if not token_nonce or not isinstance(token_nonce, str):
        raise ValueError("Claim 'nonce' obrigatório ausente no ID Token.")

    if expected_nonce is not None:
        if not secrets.compare_digest(str(token_nonce), str(expected_nonce)):
            raise ValueError("Claim 'nonce' diverge do nonce vinculado à sessão.")

    if not claims.get("sub") or not claims.get("email"):
        raise ValueError("Claims 'sub' e 'email' são obrigatórios no ID Token.")

    return claims


oauth = OAuth()
if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

    _orig_parse_id_token = oauth.google.parse_id_token

    async def _strict_parse_id_token(
        token: Dict[str, Any],
        nonce: Optional[str] = None,
        claims_options=None,
        **kwargs,
    ):
        """
        Envolve parse_id_token do Authlib para exigir criptografia RS256, exp obrigatório,
        iss, aud e nonce em todas as validações de ID Token.
        """
        id_token_str = token.get("id_token") if isinstance(token, dict) else None
        if not id_token_str:
            raise ValueError("ID Token ausente na resposta OAuth.")

        strict_options = {
            "iss": {"essential": True, "values": list(ALLOWED_ISSUERS)},
            "aud": {"essential": True, "value": GOOGLE_CLIENT_ID},
            "exp": {"essential": True},
            "sub": {"essential": True},
            "email": {"essential": True},
            "nonce": {"essential": True},
        }
        if claims_options:
            strict_options.update(claims_options)

        claims = await _orig_parse_id_token(
            token,
            nonce=nonce,
            claims_options=strict_options,
            **kwargs,
        )
        validate_oidc_claims(dict(claims), expected_nonce=nonce, expected_aud=GOOGLE_CLIENT_ID)
        return claims

    oauth.google.parse_id_token = _strict_parse_id_token


def hash_token(raw_token: str) -> str:
    """Gera hash criptográfico SHA-256 para indexação segura de tokens no banco."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def register_oauth_handshake_state(
    db: Session,
    state: str,
    browser_bind: str,
    nonce: str,
    next_url: str = "/",
    ttl_seconds: int = OAUTH_STATE_TTL_SECONDS,
) -> None:
    """
    Registra temporariamente o state do handshake OAuth no banco SQLite do servidor,
    vinculado ao segredo do navegador (browser_bind) e com expiração curta.
    """
    if not state or not browser_bind or not nonce:
        raise ValueError("state, browser_bind e nonce são obrigatórios para registrar o handshake.")

    now_utc = datetime.now(timezone.utc)
    expires_at = now_utc + timedelta(seconds=ttl_seconds)

    iniciar_transacao_imediata(db)
    try:
        db.query(OAuthHandshakeState).filter(OAuthHandshakeState.expires_at <= now_utc).delete()
        record = OAuthHandshakeState(
            state_hash=hash_token(state),
            browser_token_hash=hash_token(browser_bind),
            nonce=nonce,
            next_url=next_url or "/",
            expires_at=expires_at,
            created_at=now_utc,
        )
        db.add(record)
        db.commit()
    except Exception:
        db.rollback()
        raise


def consume_oauth_handshake_state(
    db: Session,
    state: Optional[str],
    browser_bind: Optional[str],
    expected_nonce: Optional[str] = None,
) -> Optional[Tuple[str, str]]:
    """
    Consome atomicamente (uso único no servidor) o state do handshake OAuth.
    Mesmo que um cliente reapresente um cookie assinado antigo do SessionMiddleware,
    o registro já terá sido removido do banco de dados na primeira execução,
    bloqueando o replay antes de qualquer nova troca de código OAuth.
    Retorna (nonce, next_url) se válido, ou None caso ausente, expirado, divergente ou já consumido.
    """
    if not state or not browser_bind or not isinstance(state, str) or not isinstance(browser_bind, str):
        return None

    state_h = hash_token(state)
    browser_h = hash_token(browser_bind)
    now_utc = datetime.now(timezone.utc)

    iniciar_transacao_imediata(db)
    try:
        record = (
            db.query(OAuthHandshakeState)
            .filter(OAuthHandshakeState.state_hash == state_h)
            .first()
        )
        if not record:
            db.commit()
            return None

        rec_browser_hash = record.browser_token_hash
        rec_nonce = record.nonce
        rec_next_url = record.next_url
        rec_expires_at = record.expires_at
        if rec_expires_at.tzinfo is None:
            rec_expires_at = rec_expires_at.replace(tzinfo=timezone.utc)

        if rec_expires_at <= now_utc:
            db.delete(record)
            db.commit()
            return None

        if not secrets.compare_digest(rec_browser_hash, browser_h):
            db.commit()
            return None

        if not rec_nonce or (expected_nonce is not None and not secrets.compare_digest(rec_nonce, str(expected_nonce))):
            db.delete(record)
            db.commit()
            return None

        # Remove o state imediatamente no servidor (consumo atômico de uso único)
        db.delete(record)
        db.commit()
    except Exception:
        db.rollback()
        return None

    return rec_nonce, rec_next_url


def is_google_native_or_workspace(email: str, hd: Optional[str]) -> bool:
    """
    Verifica se a conta pertence a um domínio nativo do Google (@gmail.com / @googlemail.com)
    ou a um domínio corporativo Google Workspace validado pelo parâmetro 'hd'.
    """
    email_clean = email.strip().lower()
    if email_clean.endswith("@gmail.com") or email_clean.endswith("@googlemail.com"):
        return True
    if hd and email_clean.endswith(f"@{hd.strip().lower()}"):
        return True
    return False


def create_user_session(
    db: Session,
    user: User,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Tuple[str, UserSession]:
    """
    Cria uma nova sessão revogável no servidor.
    O cookie conterá apenas um token opaco aleatório de alta entropia.
    """
    raw_token = secrets.token_urlsafe(32)
    token_hash = hash_token(raw_token)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_MAX_AGE_HOURS)

    session_obj = UserSession(
        id=str(uuid.uuid4()),
        user_id=user.id,
        session_token_hash=token_hash,
        csrf_token=csrf_token,
        expires_at=expires_at,
        created_at=datetime.now(timezone.utc),
        ip_address=ip_address,
        user_agent=(user_agent[:255] if user_agent else None),
    )
    db.add(session_obj)
    db.commit()
    return raw_token, session_obj


def get_session_from_cookie(
    request: Request,
    db: Session,
) -> Tuple[Optional[User], Optional[UserSession]]:
    """
    Recupera o usuário e a sessão a partir do cookie.
    Se o usuário tiver sido bloqueado no banco ou a sessão expirou, revoga imediatamente.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None, None

    token_hash = hash_token(token)
    session_obj = db.query(UserSession).filter(UserSession.session_token_hash == token_hash).first()
    if not session_obj:
        return None, None

    now_utc = datetime.now(timezone.utc)
    expires_at = session_obj.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at < now_utc:
        db.delete(session_obj)
        db.commit()
        return None, None

    user = db.query(User).filter(User.id == session_obj.user_id).first()
    if not user or not user.is_active:
        db.delete(session_obj)
        db.commit()
        return user, None

    return user, session_obj


def revoke_session(db: Session, raw_token: str) -> bool:
    """Revoga uma sessão específica a partir do token bruto."""
    token_hash = hash_token(raw_token)
    session_obj = db.query(UserSession).filter(UserSession.session_token_hash == token_hash).first()
    if session_obj:
        db.delete(session_obj)
        db.commit()
        return True
    return False


def revoke_all_sessions_for_user(db: Session, user_id: int, commit: bool = True):
    """Revoga todas as sessões ativas de um usuário."""
    db.query(UserSession).filter(UserSession.user_id == user_id).delete()
    if commit:
        db.commit()


async def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Dependency que retorna o usuário autenticado ou lança 401."""
    user, session = get_session_from_cookie(request, db)
    if not user or not session or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Não autenticado ou acesso revogado.",
        )
    request.state.user = user
    request.state.session = session
    request.state.csrf_token = session.csrf_token
    return user


async def require_login(request: Request, db: Session = Depends(get_db)) -> User:
    """Dependency para rotas web: exige usuário ativo e sessão válida; redireciona para login se inválido."""
    user, session = get_session_from_cookie(request, db)
    if not user or not session or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    request.state.user = user
    request.state.session = session
    request.state.csrf_token = session.csrf_token
    return user


async def require_admin(request: Request, db: Session = Depends(get_db)) -> User:
    """Dependency que exige papel administrativo ativo e apto ou lança 401/403."""
    user, session = get_session_from_cookie(request, db)
    if not user or not session or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Não autenticado ou sessão expirada",
        )
    if user.role != "admin" or not is_admin_usable(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso proibido: privilégio de administrador necessário",
        )
    request.state.user = user
    request.state.session = session
    request.state.csrf_token = session.csrf_token
    return user


def validate_csrf_token(request: Request, csrf_token: str = Form(...)):
    """Valida token CSRF em formulários POST com comparação segura contra timing attacks."""
    expected_token = getattr(request.state, "csrf_token", None)
    if not expected_token or not csrf_token or not secrets.compare_digest(csrf_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token de segurança CSRF inválido ou expirado. Recarregue a página e tente novamente.",
        )


async def require_api_write_access(request: Request, db: Session = Depends(get_db)) -> User:
    """
    Dependency para rotas de escrita da API (POST/PUT/PATCH/DELETE em /api/*):
    Exige usuário autenticado ativo e valida token CSRF enviado via header 'X-CSRF-Token'
    ou campo de formulário 'csrf_token'.
    """
    user, session = get_session_from_cookie(request, db)
    if not user or not session or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Não autenticado ou acesso revogado.",
        )

    expected_token = session.csrf_token
    provided_token = request.headers.get("X-CSRF-Token") or request.headers.get("x-csrf-token")

    if not provided_token:
        try:
            form_data = await request.form()
            provided_token = form_data.get("csrf_token")
        except Exception:
            provided_token = None

    if (
        not expected_token
        or not provided_token
        or not isinstance(provided_token, str)
        or not secrets.compare_digest(provided_token, expected_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token de segurança CSRF ausente ou inválido.",
        )

    request.state.user = user
    request.state.session = session
    request.state.csrf_token = expected_token
    return user
