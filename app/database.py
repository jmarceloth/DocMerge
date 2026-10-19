"""
Configuração do banco de dados SQLite para autenticação, sessões revogáveis,
estados de handshake OIDC de uso único e diretórios persistentes configuráveis.
"""

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    event,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship


def resolve_data_dir() -> Path:
    """Resolve o diretório persistente base (DATA_DIR)."""
    env_data_dir = os.getenv("DATA_DIR")
    if env_data_dir:
        path = Path(env_data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    container_dir = Path("/app/data")
    if container_dir.exists() and os.access(container_dir, os.W_OK):
        return container_dir

    local_dir = Path("./data")
    local_dir.mkdir(parents=True, exist_ok=True)
    return local_dir


def resolve_database_file() -> Path:
    """Resolve o caminho do arquivo SQLite (DATABASE_PATH)."""
    db_path = os.getenv("DATABASE_PATH")
    if db_path:
        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        return db_file

    return resolve_data_dir() / "docmerge_auth.db"


def get_uploads_dir() -> Path:
    """Resolve o diretório persistente para armazenamento do timbrado padrão (UPLOADS_DIR)."""
    uploads_env = os.getenv("UPLOADS_DIR")
    if uploads_env:
        path = Path(uploads_env)
    else:
        path = resolve_data_dir() / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_letterhead_path() -> Path:
    """Resolve o caminho completo do arquivo de timbrado padrão (LETTERHEAD_PATH)."""
    letterhead_env = os.getenv("LETTERHEAD_PATH")
    if letterhead_env:
        path = Path(letterhead_env)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    return get_uploads_dir() / "default_letterhead.pdf"


DB_FILE = resolve_database_file()
DATABASE_URL = f"sqlite:///{DB_FILE.resolve()}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 15},
)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Habilita modo WAL, chaves estrangeiras e busy_timeout no SQLite."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA busy_timeout=15000;")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    """
    Modelo de usuário autorizado para acesso via Google OIDC.
    A fonte única de verdade para autorização administrativa é a coluna 'role'.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    google_sub = Column(String, unique=True, index=True, nullable=True)
    name = Column(String, nullable=True)
    picture_url = Column(String, nullable=True)
    role = Column(String, default="user", nullable=False)  # "admin" ou "user"
    is_active = Column(Boolean, default=True, nullable=False)
    auth_type = Column(String, default="google", nullable=False)  # "google" ou "legacy_disabled"
    third_party_pending = Column(Boolean, default=False, nullable=False)
    candidate_sub = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    last_login = Column(DateTime, nullable=True)

    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")

    @property
    def is_admin(self) -> bool:
        """Reflete estritamente se role == 'admin'."""
        return self.role == "admin"


class UserSession(Base):
    """
    Sessões persistentes e revogáveis no servidor.
    O cookie do cliente armazena apenas um identificador aleatório opaco.
    """
    __tablename__ = "user_sessions"

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_token_hash = Column(String(64), unique=True, nullable=False, index=True)
    csrf_token = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)

    user = relationship("User", back_populates="sessions")


class OAuthHandshakeState(Base):
    """
    Registro temporário de state/nonce no servidor para o handshake Google OIDC.
    Garante vínculo ao navegador, expiração curta e consumo atômico de uso único,
    impedindo replay mesmo se o cookie assinado do handshake for reapresentado.
    """
    __tablename__ = "oauth_handshake_states"

    state_hash = Column(String(64), primary_key=True)
    browser_token_hash = Column(String(64), nullable=False, index=True)
    nonce = Column(String(128), nullable=False)
    next_url = Column(String(512), nullable=False, default="/")
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


def init_storage():
    """
    Garante a criação do diretório persistente de timbrado e migra eventual
    timbrado legado (/app/app/uploads/default_letterhead.pdf) caso o novo caminho ainda não exista.
    Falhas críticas de criação de diretório interrompem a inicialização.
    """
    try:
        target_path = get_letterhead_path()
        target_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise RuntimeError(
            f"Falha crítica ao inicializar diretório persistente de armazenamento ({type(exc).__name__}). "
            "Verifique o mapeamento e as permissões do volume DATA_DIR/UPLOADS_DIR."
        ) from exc

    legacy_paths = [
        Path("/app/app/uploads/default_letterhead.pdf"),
        Path("app/uploads/default_letterhead.pdf"),
    ]
    if not target_path.exists():
        for legacy in legacy_paths:
            try:
                if legacy.exists() and legacy.resolve() != target_path.resolve():
                    shutil.copy2(legacy, target_path)
                    break
            except Exception as exc:
                raise RuntimeError(
                    f"Falha crítica ao migrar timbrado legado para o volume persistente ({type(exc).__name__})."
                ) from exc


def seed_initial_owner(db_session=None):
    """
    Garante acesso inicial exclusivo à conta proprietária confirmada (jmarceloth@gmail.com)
    somente quando a tabela de usuários está vazia, sem autorizar domínios ou terceiros
    e NUNCA reativando nem alterando permissões de usuários existentes.
    """
    owner_email = os.getenv("INITIAL_OWNER_EMAIL", "jmarceloth@gmail.com").strip().lower()
    if not owner_email or "@" not in owner_email or owner_email.startswith("@") or owner_email.endswith("@"):
        raise RuntimeError(
            "Falha crítica na inicialização: variável INITIAL_OWNER_EMAIL inválida ou ausente."
        )

    close_after = False
    if db_session is None:
        db_session = SessionLocal()
        close_after = True

    try:
        db_session.execute(text("BEGIN IMMEDIATE;"))
        total_users = db_session.query(User).count()
        if total_users == 0:
            owner = User(
                email=owner_email,
                google_sub=None,
                role="admin",
                is_active=True,
                auth_type="google",
                third_party_pending=False,
                created_at=datetime.now(timezone.utc),
            )
            db_session.add(owner)
        db_session.commit()
    except Exception as exc:
        db_session.rollback()
        raise RuntimeError(
            f"Falha crítica ao inicializar cadastro do proprietário no banco de dados ({type(exc).__name__}). "
            "Verifique a integridade e as permissões do arquivo SQLite."
        ) from exc
    finally:
        if close_after:
            db_session.close()


def init_db(seed_owner: bool = True, bind_engine=None):
    """
    Inicializa tabelas do banco de dados, diretórios persistentes e proprietário inicial.
    Qualquer falha crítica impede a inicialização com diagnóstico seguro (sem expor segredos).
    """
    target_engine = bind_engine or engine
    try:
        Base.metadata.create_all(bind=target_engine)
    except Exception as exc:
        raise RuntimeError(
            f"Falha crítica ao inicializar esquema do banco de dados SQLite ({type(exc).__name__}). "
            "Verifique o caminho e as permissões de escrita em DATA_DIR/DATABASE_PATH."
        ) from exc

    init_storage()
    env_mode = os.getenv("ENVIRONMENT", "production").strip().lower()
    if seed_owner and env_mode != "testing":
        seed_initial_owner()


def get_db():
    """Dependency do FastAPI para obter sessão do banco de dados."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
