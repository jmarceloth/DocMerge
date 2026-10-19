# AGENTS.md — Diretrizes e Contexto Técnico do DocMerge

Este documento registra a arquitetura, comandos reais, estado atual, decisões de migração, requisitos de segurança e instruções de operação do projeto **DocMerge** (`jmarceloth/DocMerge`).

---

## 1. Papéis e Governança de Desenvolvimento

- **Programador Principal:** Antigravity (responsável pela análise, implementação, testes e documentação).
- **Revisor Técnico:** Codex (revisa arquitetura, código e implantação).
- **Regras de Segurança e Operação:**
  - **Nunca** versionar segredos (`.env`, chaves OAuth, tokens, credenciais) nem documentos reais de produção (`timbrado_padrao.pdf`, bancos `.db`).
  - **Nunca** executar operações Git destrutivas (`reset --hard`, `clean -fd`, `push --force`, etc.) sem autorização explícita.
  - **Nunca** realizar `commit`, `push`, `merge` ou `deploy` sem solicitação explícita do usuário.
  - Preservar a instalação antiga em produção (no endereço `.cloud`) até que a nova instalação na Oracle ARM64 (`https://docmerge.ielectric.app`) esteja integralmente validada.

---

## 2. Fatos Confirmados e Arquitetura Implementada

### 2.1. Stack, Runtime e Compatibilidade ARM64
- **Runtime e Servidor:** Python 3.11 (`python:3.11-slim` no `Dockerfile`), FastAPI `0.104.1`, Uvicorn `0.24.0` (`uvicorn[standard]==0.24.0`), `python-multipart==0.0.6`, Jinja2 `3.1.2`.
- **Autenticação, Banco de Dados e HTTP:** `SQLAlchemy==2.0.25` (SQLite com `PRAGMA journal_mode=WAL`, `busy_timeout=15000`, `foreign_keys=ON` e transações `BEGIN IMMEDIATE` para operações críticas), `authlib==1.8.0`, `httpx==0.27.0`, `itsdangerous==2.1.2`.
- **Processamento de PDF e Imagem:**
  - `PyMuPDF==1.23.8` (`import fitz`) para leitura, validação estrutural, mesclagem de páginas, sobreposição de timbrado, inserção de numeração e compressão estrutural (`try/finally` garantindo fechamento de descritores).
  - `Pillow==10.1.0` (`PIL`) para redimensionamento (`LANCZOS`), conversão de espaço de cor (`RGBA` -> `RGB` com fundo branco) e recompressão JPEG das imagens extraídas dos PDFs.
- **Compatibilidade `linux/arm64` (`aarch64`):**
  - Verificada via metadados oficiais do PyPI para Python 3.11 (`cp311` / `manylinux2014_aarch64`): todas as dependências compiladas (`PyMuPDF==1.23.8`, `Pillow==10.1.0`, `SQLAlchemy==2.0.25`, `cryptography`) possuem wheels binárias pré-compiladas para ARM64.
  - Container configurado em `Dockerfile` para executar como usuário sem privilégios (`appuser`, UID `1000`, GID `1000`) na porta `8000`, com `--proxy-headers` e `--forwarded-allow-ips=*`.

### 2.2. Autenticação Google OIDC, Sessões no Servidor e Controle de Acesso (RBAC)
- **Validação Estrita de Configuração em Produção (`validate_runtime_config` em `app/auth.py`):**
  - Se `ENVIRONMENT` não for definido, assume `production` (sem fallback silencioso para modo de desenvolvimento).
  - Quando `ENVIRONMENT=production`, exige `BASE_URL` HTTPS pública válida (sem fallback para `localhost`), `SECRET_KEY` forte (`>= 32` caracteres e fora da lista de chaves padrão), `GOOGLE_REDIRECT_URI` HTTPS válida correspondente a `${BASE_URL}/auth/callback` e credenciais reais `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET`.
- **Validação Criptográfica OIDC e Consumo Atômico de `state` no Servidor (`app/auth.py`, `app/routers/auth.py`, `app/database.py`):**
  - Valida assinatura assimétrica (`RS256`) do `id_token` via JWKS oficial do Google (`https://www.googleapis.com/oauth2/v3/certs`), emissor (`https://accounts.google.com` ou `accounts.google.com`), `aud` igual ao `GOOGLE_CLIENT_ID`, expiração obrigatória (`exp` marcado como `essential: True`), `nonce` obrigatório e `email_verified === True`.
  - No início do login (`GET /auth/google`), o servidor gera `state`, `nonce` e `browser_bind` imprevisíveis (`secrets.token_urlsafe(32)`), grava o hash SHA-256 do `state` e do `browser_bind` na tabela `oauth_handshake_states` no SQLite com expiração curta (`600s`) e vincula ao navegador pelo cookie assinado `docmerge_oauth_session` (`HttpOnly`, `SameSite=Lax`, `Secure` em produção).
  - No callback (`GET /auth/callback`), **antes** de realizar a troca do código OAuth (`authorize_access_token`), o servidor exige a presença de `state`, `nonce` e `browser_bind` e consome atomicamente (`BEGIN IMMEDIATE` + `DELETE`) o registro em `oauth_handshake_states`. Mesmo que o cookie original do handshake seja reapresentado em tentativa de replay, a requisição é rejeitada antes de qualquer nova troca de código.
- **Whitelist Estrita, Inicialização Fail-Fast e Conta Proprietária Inicial (`app/database.py`, `app/services/user_service.py`):**
  - `init_db`, `init_storage` e `seed_initial_owner` interrompem a inicialização caso ocorra falha de criação de tabelas, diretório persistente ou seed inicial, emitindo diagnóstico seguro sem expor credenciais.
  - Quando a tabela `users` está vazia, `seed_initial_owner` cadastra exclusivamente a conta proprietária confirmada (`jmarceloth@gmail.com`, `role="admin"`, `is_active=True`), **sem jamais reativar nem alterar permissões de usuários existentes** e sem autorizar automaticamente outras contas ou domínios (`hd`).
- **Sessões Persistidas no Servidor e Proteção CSRF (`app/database.py`, `app/auth.py`):**
  - No login bem-sucedido, é gerado um token opaco (`secrets.token_urlsafe(32)`), cujo hash SHA-256 (`session_token_hash`) é gravado na tabela `user_sessions` junto com `csrf_token` e `expires_at`.
  - Cookie de sessão `docmerge_session` configurado com `HttpOnly=True`, `SameSite="lax"` e `Secure=True` (em produção).
  - `localStorage` **não** é utilizado como autoridade de autenticação, sessão ou timbrado.
  - Logout (`POST /logout`) exige CSRF válido, remove o registro da sessão no SQLite e limpa o cookie.
  - Todas as operações de escrita exigem token CSRF válido vinculado à sessão (`X-CSRF-Token` nas rotas `/api/*` e campo `csrf_token` nos formulários HTML).

### 2.3. Rotas da Aplicação e Níveis de Proteção
1. **Rotas Públicas (sem exposição de dados sensíveis):**
   - `GET /health` e `GET /healthz`: Retornam `{"status": "healthy", "version": "2.1.0"}`.
   - `GET /login`: Página de login institucional IELECTRIC com aviso de acesso restrito.
   - `GET /auth/google` e `GET /auth/callback`: Handshake OAuth2/OIDC com o Google.
   - `GET /static/*`: Fontes (`Inter`, `JetBrains Mono`), `favicon.svg` e `tema-ielectric.css`.
2. **Rotas Protegidas (Exigem Sessão Autenticada e Usuário Ativo):**
   - `GET /`: Interface principal do DocMerge (`app/templates/index.html`) alinhada ao padrão visual corporativo IELECTRIC (`DimenSAVE`, `GDrive-IElectric`, `TechReports`).
   - `POST /logout`: Encerramento de sessão no servidor (exige CSRF).
   - `GET /api/letterhead/`: Consulta existência e tamanho do timbrado padrão no servidor.
   - `GET /api/letterhead/download`: Download autenticado do timbrado padrão (`timbrado_padrao.pdf`).
   - `POST /api/letterhead/`: Upload/substituição atômica do timbrado padrão com arquivo temporário exclusivo no mesmo diretório, `fsync`, `os.replace` e limpeza garantida em `finally` (exige CSRF).
   - `DELETE /api/letterhead/`: Exclusão do timbrado padrão no servidor (exige CSRF).
   - `POST /api/merge/`: Mescla conteúdo PDF com timbrado enviado ou timbrado padrão do servidor, aplica escala (`0.01` a `1.0`) e numeração `X / N` (exige CSRF).
   - `POST /api/combine/`: Combina múltiplos PDFs na ordem enviada (exige CSRF).
   - `POST /api/optimize/`: Otimiza PDF recomprimindo imagens maiores que `1920px` em JPEG com qualidade configurável (`60` a `95`) (exige CSRF).
3. **Rotas Administrativas (Exigem `role == "admin"` + CSRF nas escritas):**
   - `GET /admin/usuarios`: Painel de gestão de usuários autorizados.
   - `POST /admin/usuarios/adicionar`, `POST /admin/usuarios/bloquear`, `POST /admin/usuarios/reativar`, `POST /admin/usuarios/alterar-papel`, `POST /admin/usuarios/confirmar-terceiro`, `POST /admin/usuarios/remover`: Gerenciamento de whitelist com trava transacional (`BEGIN IMMEDIATE`) que impede remover/desativar/rebaixar o último administrador ativo e revoga imediatamente todas as sessões de usuários bloqueados.

### 2.4. Persistência, Limites de Upload e Validação de PDF
- **Diretório Persistente Configurável (`app/database.py`):**
  - `DATA_DIR` (padrão `./data` localmente; `/app/data` no container).
  - `DATABASE_PATH`: `<DATA_DIR>/docmerge_auth.db` (SQLite com tabelas `users`, `user_sessions` e `oauth_handshake_states`).
  - `UPLOADS_DIR`: `<DATA_DIR>/uploads` e `LETTERHEAD_PATH`: `<DATA_DIR>/uploads/default_letterhead.pdf`.
  - Na inicialização (`init_storage`), se existir um arquivo legado em `/app/app/uploads/default_letterhead.pdf` ou `app/uploads/default_letterhead.pdf` e `LETTERHEAD_PATH` ainda não existir, ele é copiado automaticamente para o volume persistente.
- **Limites Operacionais e Validação Estrutural (`app/services/pdf_utils.py`):**
  - Leitura em chunks com rejeição imediata (`HTTP 413`) ao exceder `MAX_UPLOAD_FILE_SIZE_MB` (padrão `25 MB`) ou `MAX_TOTAL_UPLOAD_SIZE_MB` (padrão `60 MB`).
  - Limite máximo de arquivos em `/api/combine/`: `MAX_COMBINE_FILES` (padrão `20`).
  - Limite máximo de páginas por operação: `MAX_PDF_PAGES` (padrão `300`).
  - Validação obrigatória de extensão `.pdf`, cabeçalho `%PDF-`, abertura estrutural via PyMuPDF (`fitz.open`), rejeição de PDFs criptografados/protegidos por senha e exigência de ao menos 1 página válida.

### 2.5. Comandos Reais do Projeto
- **Execução Local (Desenvolvimento):**
  ```bash
  python -m venv .venv
  # Windows: .\.venv\Scripts\activate | Linux: source .venv/bin/activate
  pip install -r requirements.txt
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
  ```
- **Suíte de Testes Automatizados (`pytest`):**
  ```bash
  pytest tests/ -v
  ```
- **Gerenciamento de Usuários via CLI:**
  ```bash
  python gerenciar_usuarios.py inicializar-proprietario jmarceloth@gmail.com
  python gerenciar_usuarios.py listar
  python gerenciar_usuarios.py autorizar usuario@dominio.com --role user
  ```
- **Build e Execução via Docker (Oracle ARM64 / Produção):**
  ```bash
  docker build -t docmerge .
  docker run -p 8000:8000 --env-file .env -v docmerge_data:/app/data docmerge
  ```

---

## 3. Guia de Implantação na Oracle ARM64 (Coolify) e Reversão

1. **Google Cloud Console (OAuth 2.0):**
   - Origem JavaScript autorizada: `https://docmerge.ielectric.app`
   - URI de redirecionamento autorizado: `https://docmerge.ielectric.app/auth/callback`
2. **Volume Persistente e Permissões no Coolify (Oracle ARM64):**
   - Mapear `/data/coolify/applications/<app-uuid>/data` (no host) -> `/app/data` (no container).
   - Ajustar propriedade no host para o usuário não-root do container (`UID 1000:1000`):
     ```bash
     sudo mkdir -p /data/coolify/applications/<app-uuid>/data/uploads
     sudo chown -R 1000:1000 /data/coolify/applications/<app-uuid>/data
     sudo chmod -R 750 /data/coolify/applications/<app-uuid>/data
     ```
3. **Variáveis de Ambiente no Coolify (alinhadas ao código):**
   - `PORT=8000`
   - `ENVIRONMENT=production`
   - `BASE_URL=https://docmerge.ielectric.app`
   - `CORS_ORIGINS=https://docmerge.ielectric.app`
   - `TRUSTED_PROXIES=127.0.0.1,::1,172.18.0.1`
   - `SECRET_KEY=<64-hex-chars>`
   - `SESSION_MAX_AGE_HOURS=8`
   - `GOOGLE_CLIENT_ID=<client-id>.apps.googleusercontent.com`
   - `GOOGLE_CLIENT_SECRET=<client-secret>`
   - `GOOGLE_REDIRECT_URI=https://docmerge.ielectric.app/auth/callback`
   - `INITIAL_OWNER_EMAIL=jmarceloth@gmail.com`
   - `DATA_DIR=/app/data`
   - `DATABASE_PATH=/app/data/docmerge_auth.db`
   - `UPLOADS_DIR=/app/data/uploads`
   - `LETTERHEAD_PATH=/app/data/uploads/default_letterhead.pdf`
   - `MAX_UPLOAD_FILE_SIZE_MB=25`
   - `MAX_TOTAL_UPLOAD_SIZE_MB=60`
   - `MAX_COMBINE_FILES=20`
   - `MAX_PDF_PAGES=300`
4. **Restauração do Timbrado Padrão e Plano de Reversão:**
   - Após deploy e login com `jmarceloth@gmail.com`, enviar `timbrado_padrao.pdf` pela aba **Aplicar Timbrado** (ou copiar para `/data/coolify/applications/<app-uuid>/data/uploads/default_letterhead.pdf` com `chown 1000:1000`).
   - **Reversão para a instalação antiga (`.cloud`):** A instalação antiga permanece intacta em seu endereço `.cloud` original durante toda a validação. Retornar os usuários ao endereço `.cloud` preservado é imediato e não depende de alteração de DNS do domínio `.app`.
   - **Observação sobre o domínio `.app`:** Como `docmerge.ielectric.app` não existia no servidor antigo, reutilizar o endereço `.app` apontando para o servidor antigo exigiria configurar explicitamente o domínio `docmerge.ielectric.app`, o proxy reverso e o certificado TLS na origem antiga antes de alterar o DNS.
