# ⚡ IELECTRIC DocMerge (v2.1.0)

Ferramentas corporativas profissionais para manipulação e padronização de documentos PDF da **IELECTRIC Engenharia Elétrica & Automação**:
- **Aplicar Timbrado (`/api/merge/`)**: Sobrepõe páginas de conteúdo a um papel timbrado corporativo (padrão persistido no servidor ou enviado avulso), com ajuste de escala (`50%` a `100%`) e numeração de páginas (`X / N`).
- **Combinar Arquivos (`/api/combine/`)**: Concatena múltiplos documentos PDF na ordem definida pelo usuário.
- **Otimizar PDF (`/api/optimize/`)**: Redimensiona imagens de alta resolução (`> 1920px`) e recomprime streams e imagens em JPEG com controle de qualidade (`60%` a `95%`).

---

## 🔒 Segurança e Autenticação

- **Google OpenID Connect (OIDC) no Servidor**:
  - Validação criptográfica RS256 via JWKS da Google (`iss`, `aud`, `exp` obrigatório, `nonce` obrigatório, `email_verified` e `sub`).
  - `state` imprevisível, vinculado ao navegador (`oauth_browser_bind`) e registrado temporariamente no SQLite do servidor (`oauth_handshake_states`) com expiração e **consumo atômico de uso único antes da troca de código**, impedindo replay mesmo que o cookie do handshake seja reapresentado.
- **Lista de Acesso Estrita (Whitelist / RBAC)**:
  - Acesso inicial exclusivo da conta proprietária confirmada (`jmarceloth@gmail.com`) quando o banco está vazio, sem reativar nem alterar permissões de usuários existentes.
  - Nenhuma outra conta Google ou domínio é autorizado automaticamente.
- **Sessões Persistidas no Servidor e Proteção CSRF**:
  - Sessões armazenadas em SQLite (`user_sessions`) com hash criptográfico SHA-256, expiração e revogação imediata no logout ou bloqueio.
  - Cookies `HttpOnly`, `SameSite=Lax` e `Secure` em produção.
  - Token CSRF obrigatório em todas as operações de escrita (`POST` e `DELETE`).
- **Limites de Upload, Substituição Atômica e Validação Estrutural de PDFs**:
  - Leitura em blocos com limite máximo por arquivo (`MAX_UPLOAD_FILE_SIZE_MB=25`), limite total por requisição (`MAX_TOTAL_UPLOAD_SIZE_MB=60`), limite de arquivos combinados (`MAX_COMBINE_FILES=20`) e limite de páginas (`MAX_PDF_PAGES=300`).
  - Upload do timbrado padrão utiliza arquivo temporário exclusivo no mesmo diretório (`NamedTemporaryFile`), `fsync`, substituição atômica (`os.replace`) e limpeza garantida em `finally`.
  - Verificação de assinatura `%PDF-`, integridade via PyMuPDF e rejeição de PDFs criptografados por senha.

---

## ⚙️ Variáveis de Ambiente

Consulte o modelo em [`.env.example`](.env.example). Não há fallback silencioso para `localhost` ou modo de desenvolvimento: se `ENVIRONMENT` não for definido, o sistema assume `production` e exige `BASE_URL` HTTPS pública, `SECRET_KEY` forte (`>= 32` caracteres) e credenciais Google válidas.

| Variável | Obrigatória em Produção | Exemplo / Padrão | Descrição |
| :--- | :---: | :--- | :--- |
| `ENVIRONMENT` | **Sim** | `production` | Ambiente (`production`, `development`, `testing`). Padrão: `production` |
| `BASE_URL` | **Sim** | `https://docmerge.ielectric.app` | URL pública HTTPS canônica da aplicação (sem fallback em produção) |
| `SECRET_KEY` | **Sim** | `<64 hex chars>` | Chave secreta forte (`>= 32` caracteres em produção; `openssl rand -hex 32`) |
| `GOOGLE_CLIENT_ID` | **Sim** | `...apps.googleusercontent.com` | Client ID OAuth 2.0 Web Client no Google Cloud |
| `GOOGLE_CLIENT_SECRET` | **Sim** | `GOCSPX-...` | Client Secret OAuth 2.0 no Google Cloud |
| `GOOGLE_REDIRECT_URI` | Não | `https://docmerge.ielectric.app/auth/callback` | URI de retorno OAuth (padrão derivado: `${BASE_URL}/auth/callback`) |
| `INITIAL_OWNER_EMAIL` | Não | `jmarceloth@gmail.com` | Proprietário inicial semeado apenas quando a tabela `users` está vazia |
| `SESSION_MAX_AGE_HOURS` | Não | `8` | Tempo de vida da sessão autenticada em horas |
| `CORS_ORIGINS` | Não | `https://docmerge.ielectric.app` | Origens permitidas para CORS (separadas por vírgula) |
| `TRUSTED_PROXIES` | Não | `127.0.0.1,::1,172.18.0.1` | IPs confiáveis do proxy reverso |
| `PORT` | Não | `8000` | Porta HTTP de escuta do Uvicorn |
| `DATA_DIR` | Não | `/app/data` | Diretório persistente base montado em volume |
| `DATABASE_PATH` | Não | `/app/data/docmerge_auth.db` | Caminho do banco SQLite de autenticação |
| `UPLOADS_DIR` | Não | `/app/data/uploads` | Diretório persistente de uploads |
| `LETTERHEAD_PATH` | Não | `/app/data/uploads/default_letterhead.pdf` | Caminho do timbrado padrão persistido |
| `MAX_UPLOAD_FILE_SIZE_MB` | Não | `25` | Tamanho máximo por arquivo enviado (MB) |
| `MAX_TOTAL_UPLOAD_SIZE_MB` | Não | `60` | Tamanho máximo total por requisição (MB) |
| `MAX_COMBINE_FILES` | Não | `20` | Número máximo de arquivos em `/api/combine/` |
| `MAX_PDF_PAGES` | Não | `300` | Número máximo de páginas por operação |

### URI de Redirecionamento Google Cloud Console
Configure no cliente OAuth 2.0 (Web Application):
- **Origens JavaScript autorizadas:** `https://docmerge.ielectric.app`
- **URIs de redirecionamento autorizados:** `https://docmerge.ielectric.app/auth/callback`

---

## 🚀 Execução Local e Testes

```bash
python -m venv .venv
.\.venv\Scripts\activate   # Windows
# source .venv/bin/activate # Linux/macOS

pip install -r requirements.txt

# Executar suíte de testes automatizados
pytest tests/ -v

# Gerenciar usuários via CLI
python gerenciar_usuarios.py inicializar-proprietario jmarceloth@gmail.com
python gerenciar_usuarios.py listar
python gerenciar_usuarios.py autorizar usuario@dominio.com --role user
```

---

## 📦 Volume Persistente, Migração e Reversão (Coolify / Oracle ARM64)

O container executa sob o usuário sem privilégios `appuser` (`UID 1000`, `GID 1000`).

1. No serviço do **DocMerge** no Coolify (Oracle ARM64), configure o volume persistente:
   - **Source Path (Host):** `/data/coolify/applications/<app-uuid>/data` (ou volume nomeado `docmerge-data`)
   - **Destination Path (Container):** `/app/data`
2. Ajuste as permissões do volume para o usuário `1000:1000`:
   ```bash
   sudo mkdir -p /data/coolify/applications/<app-uuid>/data/uploads
   sudo chown -R 1000:1000 /data/coolify/applications/<app-uuid>/data
   sudo chmod -R 750 /data/coolify/applications/<app-uuid>/data
   ```
3. Para restaurar o timbrado padrão na nova instância:
   - Faça login em `https://docmerge.ielectric.app` com `jmarceloth@gmail.com` e envie o timbrado na aba **Aplicar Timbrado**, ou copie o arquivo para `/data/coolify/applications/<app-uuid>/data/uploads/default_letterhead.pdf` com `chown 1000:1000`.
4. **Plano de Reversão (Rollback):**
   - **Retorno imediato ao endereço antigo (`.cloud`):** A instalação antiga permanece preservada e intacta em seu servidor e domínio `.cloud` original. Em caso de falha na validação da nova instância em `docmerge.ielectric.app`, os usuários podem continuar utilizando imediatamente o endereço `.cloud` preservado.
   - **Uso do domínio `.app` no servidor antigo (se desejado):** Como o domínio `docmerge.ielectric.app` não existia no servidor antigo, atender `.app` na origem antiga **não** se resume a trocar o DNS; exigiria configurar o domínio `docmerge.ielectric.app`, o proxy reverso e a emissão de certificado TLS na instalação antiga antes de alterar o apontamento DNS.
