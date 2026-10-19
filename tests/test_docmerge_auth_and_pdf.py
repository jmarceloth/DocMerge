"""
Suíte Consolidada de Testes Automatizados do DocMerge:
- Autenticação Google OIDC (RS256, JWKS, iss, aud, exp obrigatório, nonce, email_verified)
- State imprevisível, vinculado ao navegador e de uso único (proteção contra replay)
- Acesso inicial exclusivo de jmarceloth@gmail.com e bloqueio de contas/domínios não autorizados
- Sessões persistidas no SQLite, expiração, revogação no logout e proteção CSRF em todas as escritas
- Proteção de todas as rotas (UI, merge, combine, optimize, letterhead info/upload/download/delete, admin)
- Processamento de PDFs com documentos sintéticos em memória (merge, combine, optimize) e limites de upload
"""

import io
import os
import time
import urllib.parse
import multiprocessing
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import fitz
import httpx
import pytest
from PIL import Image
from authlib.jose import JsonWebKey, jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.database as app_db_mod
import app.middleware as app_mw_mod
from app.database import Base, User, UserSession, get_db, seed_initial_owner
from app.auth import oauth, SESSION_COOKIE_NAME
from app.main import app
from app.services.user_service import (
    count_usable_admins,
    vincular_google_sub_condicional,
    registrar_pendencia_terceiro_condicional,
    confirmar_candidato_terceiro_atomico,
)


def _worker_process_rebaixar_admin(db_path_str: str, admin_id: int, result_queue):
    """Worker em processo separado para testar atomicidade BEGIN IMMEDIATE do último admin."""
    os.environ["SECRET_KEY"] = "mock-secret-key-process-1234567890"
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.services.user_service import alterar_papel_atomico

    engine_proc = create_engine(
        f"sqlite:///{db_path_str}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    SessionProc = sessionmaker(bind=engine_proc)
    with SessionProc() as db:
        try:
            alterar_papel_atomico(db, admin_id, "user")
            result_queue.put(("OK", admin_id))
        except Exception as e:
            result_queue.put(("ERROR", str(e)))
    engine_proc.dispose()


def create_synthetic_pdf(pages: int = 1, text_prefix: str = "DocFicticio", encrypt_password: str = None) -> bytes:
    """Gera um documento PDF fictício sintético em memória (sem usar arquivos reais)."""
    doc = fitz.open()
    try:
        for i in range(pages):
            page = doc.new_page(width=595, height=842)
            page.insert_text(fitz.Point(72, 100), f"{text_prefix} - Pagina {i + 1}", fontsize=14)
        if encrypt_password:
            return doc.tobytes(
                encryption=fitz.PDF_ENCRYPT_AES_256,
                owner_pw=encrypt_password,
                user_pw=encrypt_password,
            )
        return doc.tobytes()
    finally:
        doc.close()


def create_synthetic_pdf_with_large_image() -> bytes:
    """Gera um PDF fictício contendo uma imagem RGBA grande (2200x1600) para testar a otimização."""
    img = Image.new("RGBA", (2200, 1600), (40, 100, 200, 255))
    img_buf = io.BytesIO()
    img.save(img_buf, format="PNG")
    img_bytes = img_buf.getvalue()

    doc = fitz.open()
    try:
        page = doc.new_page(width=595, height=842)
        page.insert_image(fitz.Rect(50, 50, 545, 450), stream=img_bytes)
        return doc.tobytes()
    finally:
        doc.close()


@pytest.fixture(scope="session")
def rsa_keypair():
    """Gera par de chaves RSA-2048 e JWKS para validação criptográfica real com Authlib."""
    key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    jwk_pub = key.as_dict(is_private=False)
    jwk_pub["kid"] = "docmerge-test-key-1"
    jwk_pub["alg"] = "RS256"
    jwk_pub["use"] = "sig"
    return {
        "private_key": key,
        "jwk_pub": jwk_pub,
        "jwks": {"keys": [jwk_pub]},
    }


def gerar_id_token_assinado(
    rsa_keypair,
    sub="sub-owner-123",
    email="jmarceloth@gmail.com",
    iss="https://accounts.google.com",
    aud="mock-docmerge-client-id.apps.googleusercontent.com",
    exp_delta_seconds=3600,
    include_exp=True,
    nonce="test-nonce",
    include_nonce=True,
    email_verified=True,
    name="João Marcelo",
    picture="https://avatar.google.com/jm",
):
    """Gera um ID Token JWT genuinamente assinado com a chave RSA local."""
    now = int(time.time())
    header = {"alg": "RS256", "kid": rsa_keypair["jwk_pub"]["kid"]}
    payload = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "email": email,
        "email_verified": email_verified,
        "name": name,
        "picture": picture,
        "iat": now,
    }
    if include_exp:
        payload["exp"] = now + exp_delta_seconds
    if include_nonce and nonce is not None:
        payload["nonce"] = nonce
    return jwt.encode(header, payload, rsa_keypair["private_key"]).decode("utf-8")


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    """Configura banco SQLite e diretório de uploads temporários isolados por teste."""
    db_file = tmp_path / "test_docmerge_auth.db"
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    letterhead_file = uploads_dir / "default_letterhead.pdf"

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    monkeypatch.setenv("UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setenv("LETTERHEAD_PATH", str(letterhead_file))

    test_db_url = f"sqlite:///{db_file.resolve()}"
    test_engine = create_engine(
        test_db_url,
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    Base.metadata.create_all(bind=test_engine)

    orig_db_session = app_db_mod.SessionLocal
    orig_mw_session = app_mw_mod.SessionLocal

    app_db_mod.SessionLocal = TestingSessionLocal
    app_mw_mod.SessionLocal = TestingSessionLocal

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, follow_redirects=False)

    yield {
        "client": client,
        "db_session_factory": TestingSessionLocal,
        "db_file": db_file,
        "letterhead_file": letterhead_file,
    }

    app.dependency_overrides.clear()
    app_db_mod.SessionLocal = orig_db_session
    app_mw_mod.SessionLocal = orig_mw_session
    test_engine.dispose()


def _initiate_google_handshake(client) -> tuple[str, str, str]:
    """Inicia GET /auth/google e retorna (state, nonce, original_handshake_cookie)."""
    oauth.google.server_metadata.update(
        {
            "issuer": "https://accounts.google.com",
            "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_endpoint": "https://oauth2.googleapis.com/token",
            "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
            "response_types_supported": ["code", "token", "id_token"],
        }
    )
    res_init = client.get("/auth/google")
    assert res_init.status_code == 302
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(res_init.headers["location"]).query)
    state = qs["state"][0]
    nonce = qs["nonce"][0]
    handshake_cookie = res_init.cookies.get("docmerge_oauth_session", "")
    return state, nonce, handshake_cookie


def login_test_user(client, SessionFactory, email="jmarceloth@gmail.com", role="admin"):
    """Autentica um usuário de teste via fluxo completo (/auth/google -> /auth/callback) e retorna (cookies, csrf_token)."""
    with SessionFactory() as db:
        u = db.query(User).filter(User.email == email).first()
        if not u:
            u = User(email=email, role=role, is_active=True, auth_type="google")
            db.add(u)
            db.commit()

    state, nonce, _ = _initiate_google_handshake(client)
    mock_token = {
        "userinfo": {
            "sub": f"sub-{email}",
            "email": email,
            "email_verified": True,
            "name": "Usuário Autenticado",
            "nonce": nonce,
        }
    }
    with patch.object(oauth.google, "authorize_access_token", new_callable=AsyncMock, return_value=mock_token):
        res = client.get(f"/auth/callback?code=code-test&state={state}")
    assert res.status_code == 302
    cookies = res.cookies

    with SessionFactory() as db:
        sess = db.query(UserSession).order_by(UserSession.created_at.desc()).first()
        csrf_token = sess.csrf_token

    return cookies, csrf_token


# ==============================================================================
# 1. VALIDAÇÃO CRIPTOGRÁFICA OIDC REAL (ASSINATURA, ISS, AUD, EXP OBRIGATÓRIO, NONCE)
# ==============================================================================


@pytest.mark.asyncio
async def test_oidc_crypto_valid_token(rsa_keypair):
    id_token = gerar_id_token_assinado(rsa_keypair, nonce="nonce-ok-123")
    with patch.object(oauth.google, "fetch_jwk_set", return_value=rsa_keypair["jwks"]):
        claims = await oauth.google.parse_id_token({"id_token": id_token}, nonce="nonce-ok-123")
        assert claims["email"] == "jmarceloth@gmail.com"
        assert claims["iss"] == "https://accounts.google.com"


@pytest.mark.asyncio
async def test_oidc_crypto_forged_signature_rejected(rsa_keypair):
    other_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    header = {"alg": "RS256", "kid": rsa_keypair["jwk_pub"]["kid"]}
    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": "mock-docmerge-client-id.apps.googleusercontent.com",
        "sub": "sub-1",
        "email": "jmarceloth@gmail.com",
        "iat": now,
        "exp": now + 3600,
        "nonce": "n1",
    }
    forged = jwt.encode(header, payload, other_key).decode("utf-8")
    with patch.object(oauth.google, "fetch_jwk_set", return_value=rsa_keypair["jwks"]):
        with pytest.raises(Exception):
            await oauth.google.parse_id_token({"id_token": forged}, nonce="n1")


@pytest.mark.asyncio
async def test_oidc_crypto_invalid_issuer_aud_exp_nonce_rejected(rsa_keypair):
    with patch.object(oauth.google, "fetch_jwk_set", return_value=rsa_keypair["jwks"]):
        # Emissor inválido
        t_iss = gerar_id_token_assinado(rsa_keypair, iss="https://evil.example.com", nonce="n1")
        with pytest.raises(Exception):
            await oauth.google.parse_id_token({"id_token": t_iss}, nonce="n1")

        # Audience inválida
        t_aud = gerar_id_token_assinado(rsa_keypair, aud="outro-client.apps.googleusercontent.com", nonce="n1")
        with pytest.raises(Exception):
            await oauth.google.parse_id_token({"id_token": t_aud}, nonce="n1")

        # Token expirado
        t_exp = gerar_id_token_assinado(rsa_keypair, exp_delta_seconds=-3600, nonce="n1")
        with pytest.raises(Exception):
            await oauth.google.parse_id_token({"id_token": t_exp}, nonce="n1")

        # Token sem claim exp (expiração obrigatória!)
        t_no_exp = gerar_id_token_assinado(rsa_keypair, include_exp=False, nonce="n1")
        with pytest.raises(Exception):
            await oauth.google.parse_id_token({"id_token": t_no_exp}, nonce="n1")

        # Nonce divergente ou ausente
        t_nonce = gerar_id_token_assinado(rsa_keypair, nonce="nonce-correto")
        with pytest.raises(Exception):
            await oauth.google.parse_id_token({"id_token": t_nonce}, nonce="nonce-errado")

        t_no_nonce = gerar_id_token_assinado(rsa_keypair, include_nonce=False)
        with pytest.raises(Exception):
            await oauth.google.parse_id_token({"id_token": t_no_nonce}, nonce="nonce-correto")


# ==============================================================================
# 2. STATE VINCULADO AO NAVEGADOR E DE USO ÚNICO NO SERVIDOR (PROTEÇÃO CONTRA REPLAY)
# ==============================================================================


def test_oidc_full_flow_browser_bound_state_and_single_use(test_env, rsa_keypair):
    client = test_env["client"]
    Session = test_env["db_session_factory"]

    with Session() as db:
        seed_initial_owner(db)

    oauth.google.server_metadata.clear()
    token_container = {"exchange_calls": 0}

    async def fake_google_http_send(self, request, *args, **kwargs):
        url = str(request.url)
        if "openid-configuration" in url:
            return httpx.Response(
                200,
                json={
                    "issuer": "https://accounts.google.com",
                    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
                    "token_endpoint": "https://oauth2.googleapis.com/token",
                    "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
                    "response_types_supported": ["code", "token", "id_token"],
                },
                request=request,
            )
        if "oauth2.googleapis.com/token" in url:
            token_container["exchange_calls"] += 1
            return httpx.Response(200, json=token_container["token_response"], request=request)
        if "oauth2/v3/certs" in url:
            return httpx.Response(200, json=rsa_keypair["jwks"], request=request)
        return httpx.Response(404, request=request)

    with patch.object(httpx.AsyncClient, "send", fake_google_http_send):
        res_init = client.get("/auth/google")
        assert res_init.status_code == 302
        original_handshake_cookie = res_init.cookies.get("docmerge_oauth_session")
        assert original_handshake_cookie

        qs = urllib.parse.parse_qs(urllib.parse.urlparse(res_init.headers["location"]).query)
        state = qs["state"][0]
        nonce = qs["nonce"][0]

        id_token = gerar_id_token_assinado(
            rsa_keypair,
            sub="sub-jmarcelo-1",
            email="jmarceloth@gmail.com",
            nonce=nonce,
        )
        token_container["token_response"] = {
            "access_token": "ya29.simulated",
            "token_type": "Bearer",
            "expires_in": 3600,
            "id_token": id_token,
        }

        # 1. Tentativa sem state ou de outro navegador sem o cookie de handshake é rejeitada ANTES da troca de código
        other_browser = TestClient(app, follow_redirects=False)
        assert other_browser.get("/auth/callback?code=code123").status_code == 302
        res_other_browser = other_browser.get(f"/auth/callback?code=code123&state={state}")
        assert res_other_browser.status_code == 302
        assert "erro=oauth_falha" in res_other_browser.headers["location"]
        assert token_container["exchange_calls"] == 0

        # 2. Navegador legítimo conclui o callback na 1ª vez (1 troca de código)
        res_cb = client.get(f"/auth/callback?code=code123&state={state}")
        assert res_cb.status_code == 302
        assert res_cb.headers["location"] == "/"
        assert SESSION_COOKIE_NAME in res_cb.cookies
        assert token_container["exchange_calls"] == 1

        # 3. Replay reapresentando o COOKIE ORIGINAL do handshake após callback concluído:
        # deve ser rejeitado pelo banco do servidor ANTES de realizar uma nova troca de código!
        client.cookies.set("docmerge_oauth_session", original_handshake_cookie)
        res_replay_with_original_cookie = client.get(f"/auth/callback?code=code123&state={state}")
        assert res_replay_with_original_cookie.status_code == 302
        assert "erro=oauth_falha" in res_replay_with_original_cookie.headers["location"]
        assert token_container["exchange_calls"] == 1  # Nenhuma nova troca de código foi executada!


# ==============================================================================
# 3. ACESSO INICIAL EXCLUSIVO DE jmarceloth@gmail.com E WHITELIST ESTRITA
# ==============================================================================


def test_initial_owner_exclusive_and_unauthorized_accounts_rejected(test_env):
    client = test_env["client"]
    Session = test_env["db_session_factory"]

    with Session() as db:
        seed_initial_owner(db)
        users = db.query(User).all()
        assert len(users) == 1
        assert users[0].email == "jmarceloth@gmail.com"
        assert users[0].role == "admin"

    # Conta não cadastrada do Gmail ou do mesmo domínio corporativo é rejeitada
    for unauthorized_email in ("intruso@gmail.com", "outro@ielectric.app", "alguem@ielectric.com.br"):
        state, nonce, _ = _initiate_google_handshake(client)
        mock_token = {
            "userinfo": {
                "sub": f"sub-{unauthorized_email}",
                "email": unauthorized_email,
                "email_verified": True,
                "hd": "ielectric.app" if "ielectric" in unauthorized_email else None,
                "nonce": nonce,
            }
        }
        with patch.object(oauth.google, "authorize_access_token", new_callable=AsyncMock, return_value=mock_token):
            res = client.get(f"/auth/callback?code=c1&state={state}")
        assert res.status_code == 302
        assert "erro=nao_autorizado" in res.headers["location"]

    # Conta com email_verified=False é rejeitada mesmo se o e-mail for do proprietário
    state, nonce, _ = _initiate_google_handshake(client)
    unverified_token = {
        "userinfo": {
            "sub": "sub-unv",
            "email": "jmarceloth@gmail.com",
            "email_verified": False,
            "nonce": nonce,
        }
    }
    with patch.object(oauth.google, "authorize_access_token", new_callable=AsyncMock, return_value=unverified_token):
        res_unv = client.get(f"/auth/callback?code=c2&state={state}")
    assert res_unv.status_code == 302
    assert "erro=email_nao_verificado" in res_unv.headers["location"]

    # Callback com nonce ausente no token é rejeitado
    state, nonce, _ = _initiate_google_handshake(client)
    missing_nonce_token = {
        "userinfo": {
            "sub": "sub-owner",
            "email": "jmarceloth@gmail.com",
            "email_verified": True,
        }
    }
    with patch.object(oauth.google, "authorize_access_token", new_callable=AsyncMock, return_value=missing_nonce_token):
        res_no_nonce = client.get(f"/auth/callback?code=c3&state={state}")
    assert res_no_nonce.status_code == 302
    assert "erro=oauth_falha" in res_no_nonce.headers["location"]


# ==============================================================================
# 4. PROTEÇÃO DE ROTAS, SESSÃO EXPIRADA, LOGOUT E CSRF EM ESCRITAS
# ==============================================================================


def test_all_routes_protected_when_unauthenticated(test_env):
    client = test_env["client"]
    dummy_pdf = create_synthetic_pdf(1)

    # Healthcheck e página de login permanecem públicos
    assert client.get("/health").status_code == 200
    assert client.get("/login").status_code == 200

    # Interface principal redireciona para /login
    res_home = client.get("/")
    assert res_home.status_code == 302
    assert "/login" in res_home.headers["location"]

    # Rotas de upload, processamento, consulta, download e exclusão retornam 401 JSON
    assert client.get("/api/letterhead/").status_code == 401
    assert client.get("/api/letterhead/download").status_code == 401
    assert client.delete("/api/letterhead/").status_code == 401
    assert client.post(
        "/api/letterhead/",
        files={"file": ("timbrado.pdf", dummy_pdf, "application/pdf")},
    ).status_code == 401
    assert client.post(
        "/api/merge/",
        files={"content": ("c.pdf", dummy_pdf, "application/pdf")},
    ).status_code == 401
    assert client.post(
        "/api/combine/",
        files=[
            ("files", ("1.pdf", dummy_pdf, "application/pdf")),
            ("files", ("2.pdf", dummy_pdf, "application/pdf")),
        ],
    ).status_code == 401
    assert client.post(
        "/api/optimize/",
        files={"file": ("o.pdf", dummy_pdf, "application/pdf")},
    ).status_code == 401


def test_csrf_required_on_all_write_operations_and_logout_revokes_session(test_env):
    client = test_env["client"]
    Session = test_env["db_session_factory"]
    cookies, csrf_token = login_test_user(client, Session)
    dummy_pdf = create_synthetic_pdf(1)

    # Operações de escrita na API sem CSRF devem retornar 403 Forbidden
    assert client.post(
        "/api/letterhead/",
        files={"file": ("timbrado.pdf", dummy_pdf, "application/pdf")},
        cookies=cookies,
    ).status_code == 403

    assert client.delete("/api/letterhead/", cookies=cookies).status_code == 403

    assert client.post(
        "/api/merge/",
        files={"content": ("c.pdf", dummy_pdf, "application/pdf")},
        cookies=cookies,
    ).status_code == 403

    assert client.post(
        "/api/combine/",
        files=[
            ("files", ("1.pdf", dummy_pdf, "application/pdf")),
            ("files", ("2.pdf", dummy_pdf, "application/pdf")),
        ],
        cookies=cookies,
    ).status_code == 403

    assert client.post(
        "/api/optimize/",
        files={"file": ("o.pdf", dummy_pdf, "application/pdf")},
        cookies=cookies,
    ).status_code == 403

    # Logout sem CSRF ou com CSRF inválido falha
    assert client.post("/logout", cookies=cookies).status_code in (400, 422)
    assert client.post("/logout", data={"csrf_token": "token-invalido"}, cookies=cookies).status_code == 400

    # Logout com CSRF válido revoga a sessão no banco
    res_logout = client.post("/logout", data={"csrf_token": csrf_token}, cookies=cookies)
    assert res_logout.status_code == 303
    assert client.get("/", cookies=cookies).status_code == 302
    assert client.get("/api/letterhead/", cookies=cookies).status_code == 401


# ==============================================================================
# 5. PROCESSAMENTO DE PDFS (TIMBRADO, MERGE, COMBINE, OPTIMIZE) E LIMITES
# ==============================================================================


def test_pdf_letterhead_merge_combine_optimize_workflow(test_env):
    client = test_env["client"]
    Session = test_env["db_session_factory"]
    cookies, csrf_token = login_test_user(client, Session)
    headers = {"X-CSRF-Token": csrf_token}

    synthetic_letterhead = create_synthetic_pdf(pages=1, text_prefix="TimbradoFicticio")
    synthetic_content = create_synthetic_pdf(pages=2, text_prefix="ConteudoFicticio")

    # 1. Consulta inicial do timbrado padrão (não existe)
    res_info0 = client.get("/api/letterhead/", cookies=cookies)
    assert res_info0.status_code == 200
    assert res_info0.json()["exists"] is False

    # 2. Upload do timbrado padrão fictício
    res_up = client.post(
        "/api/letterhead/",
        files={"file": ("timbrado_ficticio.pdf", synthetic_letterhead, "application/pdf")},
        headers=headers,
        cookies=cookies,
    )
    assert res_up.status_code == 200
    assert "sucesso" in res_up.json()["message"]
    assert test_env["letterhead_file"].exists()

    # 3. Consulta e download do timbrado padrão
    res_info1 = client.get("/api/letterhead/", cookies=cookies)
    assert res_info1.status_code == 200
    assert res_info1.json()["exists"] is True

    res_dl = client.get("/api/letterhead/download", cookies=cookies)
    assert res_dl.status_code == 200
    assert res_dl.headers["content-type"] == "application/pdf"
    assert res_dl.content.startswith(b"%PDF-")

    # 4. Mesclar conteúdo com o timbrado padrão do servidor
    res_merge = client.post(
        "/api/merge/",
        files={"content": ("relatorio.pdf", synthetic_content, "application/pdf")},
        data={"scale": "0.85"},
        headers=headers,
        cookies=cookies,
    )
    assert res_merge.status_code == 200
    merged_doc = fitz.open(stream=res_merge.content, filetype="pdf")
    try:
        assert len(merged_doc) == 2
        assert "1 / 2" in merged_doc[0].get_text()
        assert "2 / 2" in merged_doc[1].get_text()
    finally:
        merged_doc.close()

    # 5. Combinar múltiplos PDFs
    pdf_a = create_synthetic_pdf(pages=1, text_prefix="ParteA")
    pdf_b = create_synthetic_pdf(pages=2, text_prefix="ParteB")
    res_combine = client.post(
        "/api/combine/",
        files=[
            ("files", ("a.pdf", pdf_a, "application/pdf")),
            ("files", ("b.pdf", pdf_b, "application/pdf")),
        ],
        headers=headers,
        cookies=cookies,
    )
    assert res_combine.status_code == 200
    combined_doc = fitz.open(stream=res_combine.content, filetype="pdf")
    try:
        assert len(combined_doc) == 3
        assert "ParteA" in combined_doc[0].get_text()
        assert "ParteB" in combined_doc[1].get_text()
    finally:
        combined_doc.close()

    # 6. Otimizar PDF com imagem grande
    heavy_pdf = create_synthetic_pdf_with_large_image()
    res_opt = client.post(
        "/api/optimize/",
        files={"file": ("pesado.pdf", heavy_pdf, "application/pdf")},
        data={"quality": "75"},
        headers=headers,
        cookies=cookies,
    )
    assert res_opt.status_code == 200
    opt_doc = fitz.open(stream=res_opt.content, filetype="pdf")
    try:
        assert len(opt_doc) == 1
        assert len(res_opt.content) < len(heavy_pdf)
    finally:
        opt_doc.close()

    # 7. Exclusão do timbrado padrão
    res_del = client.delete("/api/letterhead/", headers=headers, cookies=cookies)
    assert res_del.status_code == 200
    assert not test_env["letterhead_file"].exists()


def test_pdf_validation_and_upload_limits_enforced(test_env, monkeypatch):
    client = test_env["client"]
    Session = test_env["db_session_factory"]
    cookies, csrf_token = login_test_user(client, Session)
    headers = {"X-CSRF-Token": csrf_token}

    # 1. Extensão inválida (.txt)
    res_ext = client.post(
        "/api/optimize/",
        files={"file": ("invalido.txt", b"%PDF-1.4 fake", "text/plain")},
        headers=headers,
        cookies=cookies,
    )
    assert res_ext.status_code == 400

    # 2. Conteúdo não-PDF com extensão .pdf
    res_bad_magic = client.post(
        "/api/optimize/",
        files={"file": ("falso.pdf", b"Isto nao e um PDF real", "application/pdf")},
        headers=headers,
        cookies=cookies,
    )
    assert res_bad_magic.status_code == 400

    # 3. PDF protegido por senha
    encrypted_pdf = create_synthetic_pdf(pages=1, encrypt_password="secret")
    res_enc = client.post(
        "/api/optimize/",
        files={"file": ("protegido.pdf", encrypted_pdf, "application/pdf")},
        headers=headers,
        cookies=cookies,
    )
    assert res_enc.status_code == 400
    assert "senha" in res_enc.json()["detail"].lower()

    # 4. Limite de tamanho de upload excedido (HTTP 413)
    monkeypatch.setenv("MAX_UPLOAD_FILE_SIZE_MB", "0.001")  # ~1 KB
    valid_pdf = create_synthetic_pdf(pages=2)
    res_too_large = client.post(
        "/api/optimize/",
        files={"file": ("grande.pdf", valid_pdf, "application/pdf")},
        headers=headers,
        cookies=cookies,
    )
    assert res_too_large.status_code == 413


# ==============================================================================
# 6. PROTEÇÃO MULTIPROCESSO DO ÚLTIMO ADMINISTRADOR E RBAC
# ==============================================================================


def test_multiprocess_last_admin_protection(tmp_path):
    db_file = tmp_path / "mp_admin.db"
    engine_init = create_engine(f"sqlite:///{db_file.resolve()}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(bind=engine_init)

    SessionInit = sessionmaker(bind=engine_init)
    with SessionInit() as db:
        adm1 = User(email="adm1@gmail.com", role="admin", is_active=True, auth_type="google")
        adm2 = User(email="adm2@gmail.com", role="admin", is_active=True, auth_type="google")
        db.add_all([adm1, adm2])
        db.commit()
        id1, id2 = adm1.id, adm2.id
    engine_init.dispose()

    result_queue = multiprocessing.Queue()
    p1 = multiprocessing.Process(target=_worker_process_rebaixar_admin, args=(str(db_file), id1, result_queue))
    p2 = multiprocessing.Process(target=_worker_process_rebaixar_admin, args=(str(db_file), id2, result_queue))

    p1.start()
    p2.start()
    p1.join(timeout=10)
    p2.join(timeout=10)

    respostas = []
    while not result_queue.empty():
        respostas.append(result_queue.get())

    assert len([r for r in respostas if r[0] == "OK"]) == 1
    assert len([r for r in respostas if r[0] == "ERROR"]) == 1

    engine_check = create_engine(f"sqlite:///{db_file.resolve()}")
    SessionCheck = sessionmaker(bind=engine_check)
    with SessionCheck() as db:
        assert count_usable_admins(db) == 1
    engine_check.dispose()


def test_session_expiration_and_admin_rbac_revocation(test_env):
    client = test_env["client"]
    Session = test_env["db_session_factory"]

    # 1. Login de usuário comum (role='user') -> acesso permitido ao DocMerge, negado (403) ao painel admin
    user_cookies, user_csrf = login_test_user(
        client, Session, email="colaborador@gmail.com", role="user"
    )
    assert client.get("/", cookies=user_cookies).status_code == 200
    assert client.get("/admin/usuarios", cookies=user_cookies).status_code == 403

    # 2. Admin bloqueia o colaborador -> todas as sessões do colaborador são revogadas atomicamente
    admin_cookies, admin_csrf = login_test_user(
        client, Session, email="jmarceloth@gmail.com", role="admin"
    )
    with Session() as db:
        colab = db.query(User).filter(User.email == "colaborador@gmail.com").first()
        colab_id = colab.id

    res_block = client.post(
        "/admin/usuarios/bloquear",
        data={"user_id": str(colab_id), "csrf_token": admin_csrf},
        cookies=admin_cookies,
    )
    assert res_block.status_code == 303
    assert client.get("/api/letterhead/", cookies=user_cookies).status_code == 401

    # 3. Expiração da sessão no servidor invalida acesso imediatamente
    with Session() as db:
        sess = db.query(UserSession).order_by(UserSession.id.desc()).first()
        sess.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.commit()

    assert client.get("/api/letterhead/", cookies=admin_cookies).status_code == 401


# ==============================================================================
# 7. VALIDAÇÃO DE CONFIGURAÇÃO EM PRODUÇÃO, CONCORRÊNCIA DE TIMBRADO E INIT_DB FAIL-FAST
# ==============================================================================


def test_production_config_requires_https_strong_secret_and_google_creds():
    from app.auth import validate_runtime_config

    valid_prod_env = {
        "ENVIRONMENT": "production",
        "SECRET_KEY": "a" * 64,
        "BASE_URL": "https://docmerge.ielectric.app",
        "GOOGLE_REDIRECT_URI": "https://docmerge.ielectric.app/auth/callback",
        "GOOGLE_CLIENT_ID": "123456789-real.apps.googleusercontent.com",
        "GOOGLE_CLIENT_SECRET": "GOCSPX-real-secret-value-123456",
        "SESSION_MAX_AGE_HOURS": "8",
    }
    cfg = validate_runtime_config(valid_prod_env)
    assert cfg["IS_PRODUCTION"] is True
    assert cfg["BASE_URL"] == "https://docmerge.ielectric.app"
    assert cfg["GOOGLE_REDIRECT_URI"] == "https://docmerge.ielectric.app/auth/callback"

    # Sem ENVIRONMENT definido -> deve assumir 'production' (sem fallback silencioso para development)
    env_no_mode = dict(valid_prod_env)
    del env_no_mode["ENVIRONMENT"]
    assert validate_runtime_config(env_no_mode)["IS_PRODUCTION"] is True

    # Sem BASE_URL em produção -> erro (sem fallback para localhost)
    env_no_base = dict(valid_prod_env, BASE_URL="")
    with pytest.raises(ValueError, match="BASE_URL"):
        validate_runtime_config(env_no_base)

    # BASE_URL HTTP ou localhost em produção -> erro
    with pytest.raises(ValueError, match="HTTPS"):
        validate_runtime_config(dict(valid_prod_env, BASE_URL="http://docmerge.ielectric.app"))
    with pytest.raises(ValueError, match="HTTPS"):
        validate_runtime_config(dict(valid_prod_env, BASE_URL="https://localhost:8000"))

    # SECRET_KEY curta ou padrão em produção -> erro
    with pytest.raises(ValueError, match="SECRET_KEY"):
        validate_runtime_config(dict(valid_prod_env, SECRET_KEY="curta-16-chars-x"))

    # Credenciais Google ausentes ou de exemplo em produção -> erro
    with pytest.raises(ValueError, match="GOOGLE_CLIENT"):
        validate_runtime_config(dict(valid_prod_env, GOOGLE_CLIENT_ID=""))
    with pytest.raises(ValueError, match="GOOGLE_CLIENT"):
        validate_runtime_config(
            dict(valid_prod_env, GOOGLE_CLIENT_ID="seu-google-client-id.apps.googleusercontent.com")
        )


def test_concurrent_letterhead_uploads_and_guaranteed_temp_cleanup(test_env):
    from concurrent.futures import ThreadPoolExecutor
    from app.routers.letterhead import save_letterhead_atomically

    client = test_env["client"]
    Session = test_env["db_session_factory"]
    cookies, csrf_token = login_test_user(client, Session)
    headers = {"X-CSRF-Token": csrf_token}
    uploads_dir = test_env["letterhead_file"].parent

    # 1. Concorrência de uploads simultâneos sem colisão de arquivo temporário fixo
    pdfs = [create_synthetic_pdf(pages=1, text_prefix=f"TimbradoConcorrente-{i}") for i in range(8)]

    def _upload_one(idx: int):
        return client.post(
            "/api/letterhead/",
            files={"file": (f"timbrado_{idx}.pdf", pdfs[idx], "application/pdf")},
            headers=headers,
            cookies=cookies,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(_upload_one, range(8)))

    assert all(r.status_code == 200 for r in responses)
    assert test_env["letterhead_file"].exists()

    # Confirma que o PDF final é válido e que nenhum arquivo .tmp residual permaneceu no diretório
    doc = fitz.open(str(test_env["letterhead_file"]))
    try:
        assert len(doc) == 1
        assert "TimbradoConcorrente-" in doc[0].get_text()
    finally:
        doc.close()

    leftover_tmps = list(uploads_dir.glob("*.tmp"))
    assert leftover_tmps == []

    # 2. Falha simulada em os.replace garante limpeza imediata do arquivo temporário exclusivo
    with patch("app.routers.letterhead.os.replace", side_effect=OSError("Simulated replace failure")):
        with pytest.raises(OSError):
            save_letterhead_atomically(pdfs[0], test_env["letterhead_file"])

    leftover_after_error = list(uploads_dir.glob("*.tmp"))
    assert leftover_after_error == []


def test_init_db_and_seed_initial_owner_fail_fast_and_preserve_existing_users(test_env, monkeypatch):
    from app.database import init_db

    Session = test_env["db_session_factory"]

    # 1. Falha crítica na criação do banco impede inicialização sem expor credenciais
    with patch("app.database.Base.metadata.create_all", side_effect=OSError("disk full or permission denied")):
        with pytest.raises(RuntimeError, match="Falha crítica ao inicializar esquema"):
            init_db(seed_owner=False)

    # 2. INITIAL_OWNER_EMAIL inválido impede seed_initial_owner
    monkeypatch.setenv("INITIAL_OWNER_EMAIL", "email-invalido-sem-arroba")
    with Session() as db:
        with pytest.raises(RuntimeError, match="INITIAL_OWNER_EMAIL"):
            seed_initial_owner(db)
    monkeypatch.setenv("INITIAL_OWNER_EMAIL", "jmarceloth@gmail.com")

    # 3. seed_initial_owner NÃO reativa nem altera permissões de usuários existentes
    with Session() as db:
        existing_user = User(
            email="jmarceloth@gmail.com",
            google_sub="sub-existing-owner",
            role="user",
            is_active=False,
            auth_type="google",
            third_party_pending=True,
        )
        db.add(existing_user)
        db.commit()

        seed_initial_owner(db)

        reloaded = db.query(User).filter(User.email == "jmarceloth@gmail.com").first()
        assert reloaded.is_active is False
        assert reloaded.role == "user"
        assert reloaded.third_party_pending is True



