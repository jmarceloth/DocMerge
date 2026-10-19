"""
Módulo de Regras de Negócio e Gestão de Usuários, Administradores e Sessões
DocMerge

Totalmente desacoplado de chaves criptográficas web (SECRET_KEY) e OAuth.
Garante serialização estrita de transações críticas no SQLite entre processos
independentes (múltiplos workers) através de BEGIN IMMEDIATE sem supressão de erro.
"""

from datetime import datetime, timezone
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError

from app.database import User, UserSession


def iniciar_transacao_imediata(db: Session):
    """
    Inicia uma transação com bloqueio imediato exclusivo no SQLite (BEGIN IMMEDIATE).
    Garante sincronização real entre processos e workers independentes.
    """
    try:
        db.execute(text("BEGIN IMMEDIATE;"))
    except Exception as exc:
        db.rollback()
        raise RuntimeError(
            "Falha ao obter bloqueio exclusivo da transação no SQLite (database is locked). "
            "A operação concorrente foi cancelada para assegurar a consistência."
        ) from exc


def is_admin_usable(user: User) -> bool:
    """
    Verifica se o usuário é um administrador ativamente apto a utilizar o sistema.
    Contas migradas legadas (auth_type='legacy_disabled') ou com aprovação de terceiro
    pendente (third_party_pending=True) NÃO são consideradas utilizáveis.
    """
    if not user:
        return False
    return (
        user.role == "admin"
        and bool(user.is_active)
        and user.auth_type != "legacy_disabled"
        and not bool(user.third_party_pending)
    )


def count_usable_admins(db: Session, exclude_user_id: Optional[int] = None) -> int:
    """
    Conta quantos administradores aptos a acessar existem no banco.
    """
    query = db.query(User).filter(
        User.role == "admin",
        User.is_active.is_(True),
        User.auth_type != "legacy_disabled",
        User.third_party_pending.is_(False),
    )
    if exclude_user_id is not None:
        query = query.filter(User.id != exclude_user_id)
    return query.count()


def ensure_not_last_admin(db: Session, user: User, action_name: str = "modificar"):
    """
    Impede bloquear, rebaixar ou remover o único administrador apto utilizável do sistema.
    """
    if is_admin_usable(user):
        outros_aptos = count_usable_admins(db, exclude_user_id=user.id)
        if outros_aptos < 1:
            raise ValueError(
                f"Operação negada: não é permitido {action_name} o único administrador ativo e apto do sistema."
            )


def bloquear_usuario_atomico(db: Session, user_id: int) -> User:
    """
    Bloqueia um usuário e revoga TODAS as suas sessões na MESMA transação atômica.
    """
    iniciar_transacao_imediata(db)

    usuario = db.query(User).filter(User.id == user_id).first()
    if not usuario:
        db.rollback()
        raise ValueError("Usuário não encontrado.")

    ensure_not_last_admin(db, usuario, action_name="bloquear")

    usuario.is_active = False
    db.query(UserSession).filter(UserSession.user_id == user_id).delete()
    db.commit()
    return usuario


def reativar_usuario_atomico(db: Session, user_id: int) -> User:
    """
    Reativa um usuário previamente desativado.
    """
    iniciar_transacao_imediata(db)

    usuario = db.query(User).filter(User.id == user_id).first()
    if not usuario:
        db.rollback()
        raise ValueError("Usuário não encontrado.")

    usuario.is_active = True
    db.commit()
    return usuario


def alterar_papel_atomico(db: Session, user_id: int, novo_papel: str) -> User:
    """
    Altera o papel de um usuário entre 'admin' e 'user' de forma atômica e serializada.
    """
    if novo_papel not in ("admin", "user"):
        raise ValueError("Papel inválido. Deve ser 'admin' ou 'user'.")

    iniciar_transacao_imediata(db)

    usuario = db.query(User).filter(User.id == user_id).first()
    if not usuario:
        db.rollback()
        raise ValueError("Usuário não encontrado.")

    if usuario.role == "admin" and novo_papel == "user":
        ensure_not_last_admin(db, usuario, action_name="rebaixar para usuário comum")

    usuario.role = novo_papel
    db.commit()
    return usuario


def remover_usuario_atomico(db: Session, user_id: int) -> str:
    """
    Remove um usuário e todas as suas sessões de forma atômica e serializada.
    """
    iniciar_transacao_imediata(db)

    usuario = db.query(User).filter(User.id == user_id).first()
    if not usuario:
        db.rollback()
        raise ValueError("Usuário não encontrado.")

    ensure_not_last_admin(db, usuario, action_name="remover")

    email_removido = usuario.email
    db.query(UserSession).filter(UserSession.user_id == user_id).delete()
    db.delete(usuario)
    db.commit()
    return email_removido


def registrar_pendencia_terceiro_condicional(
    db: Session,
    user_id: int,
    google_sub: str,
    name: Optional[str] = None,
    picture_url: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Registra de forma condicional a pendência de aprovação de conta de terceiro.
    """
    iniciar_transacao_imediata(db)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        db.rollback()
        return False, "usuario_nao_encontrado"

    if user.third_party_pending and user.candidate_sub and user.candidate_sub != google_sub:
        db.rollback()
        return False, "candidato_conflito"

    stmt = (
        update(User)
        .where(
            User.id == user_id,
            (User.candidate_sub.is_(None) | (User.candidate_sub == google_sub)),
        )
        .values(
            third_party_pending=True,
            candidate_sub=google_sub,
            name=name or user.name,
            picture_url=picture_url or user.picture_url,
        )
    )
    res = db.execute(stmt)
    if res.rowcount == 0:
        db.rollback()
        return False, "candidato_conflito"

    db.commit()
    return True, None


def confirmar_candidato_terceiro_atomico(db: Session, user_id: int, expected_candidate_sub: str) -> User:
    """
    Confirma o vínculo de uma conta de terceiro de forma atômica e condicional no banco.
    """
    iniciar_transacao_imediata(db)

    stmt = (
        update(User)
        .where(
            User.id == user_id,
            User.third_party_pending.is_(True),
            User.candidate_sub == expected_candidate_sub,
        )
        .values(
            google_sub=expected_candidate_sub,
            candidate_sub=None,
            third_party_pending=False,
        )
    )
    res = db.execute(stmt)
    if res.rowcount == 0:
        db.rollback()
        raise ValueError(
            "A identidade candidata divergiu ou já foi alterada no banco de dados. "
            "A aprovação foi cancelada por segurança."
        )

    db.commit()
    return db.query(User).filter(User.id == user_id).first()


def vincular_google_sub_condicional(
    db: Session,
    user_id: int,
    google_sub: str,
    name: Optional[str] = None,
    picture_url: Optional[str] = None,
) -> Tuple[bool, Optional[str], Optional[User]]:
    """
    Realiza o vínculo inicial de google_sub de forma atômica e condicional (WHERE google_sub IS NULL).
    """
    iniciar_transacao_imediata(db)

    now = datetime.now(timezone.utc)
    stmt = (
        update(User)
        .where(User.id == user_id, User.google_sub.is_(None))
        .values(
            google_sub=google_sub,
            name=name,
            picture_url=picture_url,
            third_party_pending=False,
            last_login=now,
        )
    )

    try:
        res = db.execute(stmt)
        if res.rowcount == 0:
            db.rollback()
            user_reloaded = db.query(User).filter(User.id == user_id).first()
            if user_reloaded and user_reloaded.google_sub == google_sub:
                return True, None, user_reloaded
            return False, "conflito_identidade", user_reloaded

        db.commit()
        user_atualizado = db.query(User).filter(User.id == user_id).first()
        return True, None, user_atualizado

    except IntegrityError:
        db.rollback()
        return False, "sub_ja_vinculado", None
