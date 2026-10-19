"""
Utilitário CLI para Gerenciamento de Pessoas Autorizadas e Recuperação de Acesso
DocMerge

Execução: python gerenciar_usuarios.py <comando> [argumentos]
Totalmente desacoplado de SECRET_KEY e do servidor web.
"""

import sys
import argparse
from datetime import datetime, timezone
from sqlalchemy import func

from app.database import SessionLocal, User, init_db
from app.services.user_service import (
    bloquear_usuario_atomico,
    reativar_usuario_atomico,
    alterar_papel_atomico,
    remover_usuario_atomico,
    confirmar_candidato_terceiro_atomico,
)


def get_db_session():
    init_db(seed_owner=False)
    return SessionLocal()


def inicializar_proprietario(email: str = "jmarceloth@gmail.com"):
    """Inicializa o cadastro do proprietário como ADMINISTRADOR apenas se ainda não existir, sem alterar usuários existentes."""
    email_clean = email.strip().lower()
    if not email_clean or "@" not in email_clean:
        print("[ERRO] E-mail inválido fornecido.")
        sys.exit(1)

    db = get_db_session()
    try:
        user = db.query(User).filter(func.lower(User.email) == email_clean).first()
        if user:
            print(f"[INFO] Usuário '{email_clean}' já existe no banco (status e permissões preservados sem alteração).")
        else:
            novo = User(
                email=email_clean,
                google_sub=None,
                role="admin",
                is_active=True,
                auth_type="google",
                third_party_pending=False,
                created_at=datetime.now(timezone.utc),
            )
            db.add(novo)
            db.commit()
            print(f"[OK] Proprietário '{email_clean}' cadastrado com sucesso como ADMINISTRADOR inicial.")

        confirmado = db.query(User).filter(func.lower(User.email) == email_clean).first()
        status_txt = "Ativo" if confirmado.is_active else "Bloqueado"
        print(
            f"[CONFIRMAÇÃO FINAL] ID={confirmado.id} | Email='{confirmado.email}' | "
            f"Papel='{confirmado.role}' | Status='{status_txt}' | AuthType='{confirmado.auth_type}'"
        )
    finally:
        db.close()


def listar_usuarios():
    """Lista todos os usuários autorizados e seus respectivos status e vínculos."""
    db = get_db_session()
    try:
        usuarios = db.query(User).order_by(User.role.desc(), User.id.asc()).all()
        if not usuarios:
            print("[INFO] Nenhum usuário cadastrado no sistema.")
            return

        print("=" * 96)
        print(f"{'ID':<4} | {'E-MAIL':<32} | {'PAPEL':<6} | {'STATUS':<9} | {'TIPO':<8} | {'GOOGLE VÍNCULO':<22}")
        print("=" * 96)
        for u in usuarios:
            status_str = "Ativo" if u.is_active else "Bloqueado"
            tipo_str = "Legado" if u.auth_type == "legacy_disabled" else "Google"
            if u.third_party_pending:
                sub_prev = f" ({u.candidate_sub[:8]}...)" if u.candidate_sub else ""
                vinculo = f"Pendente{sub_prev}"
            elif u.google_sub:
                vinculo = f"Vinculado ({u.google_sub[:8]}...)"
            else:
                vinculo = "Aguardando 1º login"

            print(f"{u.id:<4} | {u.email:<32} | {u.role.upper():<6} | {status_str:<9} | {tipo_str:<8} | {vinculo:<22}")
        print("=" * 96)
    finally:
        db.close()


def autorizar_usuario(email: str, role: str = "user"):
    """Autoriza um novo e-mail no sistema."""
    email_clean = email.strip().lower()
    if not email_clean or "@" not in email_clean:
        print("[ERRO] E-mail inválido fornecido.")
        sys.exit(1)

    if role not in ("admin", "user"):
        role = "user"

    db = get_db_session()
    try:
        existente = db.query(User).filter(func.lower(User.email) == email_clean).first()
        if existente:
            print(f"[ERRO] O e-mail '{email_clean}' já está cadastrado no sistema.")
            sys.exit(1)

        novo = User(
            email=email_clean,
            google_sub=None,
            role=role,
            is_active=True,
            auth_type="google",
            third_party_pending=False,
            created_at=datetime.now(timezone.utc),
        )
        db.add(novo)
        db.commit()
        print(f"[OK] Usuário '{email_clean}' autorizado com sucesso como {role.upper()}.")
    finally:
        db.close()


def bloquear_usuario(email: str):
    """Bloqueia um usuário e revoga todas as suas sessões na mesma transação atômica."""
    email_clean = email.strip().lower()
    db = get_db_session()
    try:
        usuario = db.query(User).filter(func.lower(User.email) == email_clean).first()
        if not usuario:
            print(f"[ERRO] Usuário '{email_clean}' não encontrado.")
            sys.exit(1)

        bloquear_usuario_atomico(db, usuario.id)
        print(f"[OK] Usuário '{email_clean}' bloqueado e todas as suas sessões ativas foram revogadas de forma atômica.")
    except ValueError as e:
        print(f"[ERRO] {e}")
        sys.exit(1)
    finally:
        db.close()


def reativar_usuario(email: str):
    """Reativa um usuário bloqueado de forma atômica."""
    email_clean = email.strip().lower()
    db = get_db_session()
    try:
        usuario = db.query(User).filter(func.lower(User.email) == email_clean).first()
        if not usuario:
            print(f"[ERRO] Usuário '{email_clean}' não encontrado.")
            sys.exit(1)

        reativar_usuario_atomico(db, usuario.id)
        print(f"[OK] Usuário '{email_clean}' reativado com sucesso (exigirá novo login pelo Google).")
    except ValueError as e:
        print(f"[ERRO] {e}")
        sys.exit(1)
    finally:
        db.close()


def alterar_papel(email: str, novo_papel: str):
    """Altera o papel de um usuário garantindo que não seja o último admin apto."""
    email_clean = email.strip().lower()
    db = get_db_session()
    try:
        usuario = db.query(User).filter(func.lower(User.email) == email_clean).first()
        if not usuario:
            print(f"[ERRO] Usuário '{email_clean}' não encontrado.")
            sys.exit(1)

        alterar_papel_atomico(db, usuario.id, novo_papel)
        print(f"[OK] Papel do usuário '{email_clean}' alterado para {novo_papel.upper()}.")
    except ValueError as e:
        print(f"[ERRO] {e}")
        sys.exit(1)
    finally:
        db.close()


def confirmar_terceiro(email: str, expected_sub: str = None):
    """Confirma a conta Google de terceiro garantindo a correspondência do candidate_sub."""
    email_clean = email.strip().lower()
    db = get_db_session()
    try:
        usuario = db.query(User).filter(func.lower(User.email) == email_clean).first()
        if not usuario:
            print(f"[ERRO] Usuário '{email_clean}' não encontrado.")
            sys.exit(1)

        if not usuario.third_party_pending or not usuario.candidate_sub:
            print(f"[INFO] Usuário '{email_clean}' não possui vínculo de terceiro pendente.")
            return

        sub_a_confirmar = expected_sub or usuario.candidate_sub
        confirmar_candidato_terceiro_atomico(db, usuario.id, sub_a_confirmar)
        print(f"[OK] Vínculo do Google Sub ({sub_a_confirmar}) para '{email_clean}' confirmado com sucesso.")
    except ValueError as e:
        print(f"[ERRO] {e}")
        sys.exit(1)
    finally:
        db.close()


def remover_usuario(email: str):
    """Remove um usuário e suas sessões garantindo que não seja o último admin apto."""
    email_clean = email.strip().lower()
    db = get_db_session()
    try:
        usuario = db.query(User).filter(func.lower(User.email) == email_clean).first()
        if not usuario:
            print(f"[ERRO] Usuário '{email_clean}' não encontrado.")
            sys.exit(1)

        remover_usuario_atomico(db, usuario.id)
        print(f"[OK] Usuário '{email_clean}' removido permanentemente do sistema.")
    except ValueError as e:
        print(f"[ERRO] {e}")
        sys.exit(1)
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Gerenciador CLI de Usuários e Permissões - DocMerge")
    subparsers = parser.add_subparsers(dest="comando", help="Comandos disponíveis")

    p_init = subparsers.add_parser(
        "inicializar-proprietario",
        help="Inicializa o proprietário como administrador de forma idempotente",
    )
    p_init.add_argument(
        "email",
        nargs="?",
        default="jmarceloth@gmail.com",
        help="E-mail da conta Google do proprietário (padrão: jmarceloth@gmail.com)",
    )

    subparsers.add_parser("listar", help="Lista todas as pessoas cadastradas e seus status")

    p_auth = subparsers.add_parser("autorizar", help="Autoriza um novo e-mail no sistema")
    p_auth.add_argument("email", help="E-mail da conta Google a autorizar")
    p_auth.add_argument("--role", choices=["admin", "user"], default="user", help="Papel da conta (padrão: user)")

    p_block = subparsers.add_parser("bloquear", help="Bloqueia o acesso de um e-mail e revoga sessões ativas")
    p_block.add_argument("email", help="E-mail a ser bloqueado")

    p_react = subparsers.add_parser("reativar", help="Reativa um e-mail bloqueado")
    p_react.add_argument("email", help="E-mail a ser reativado")

    p_prom = subparsers.add_parser("promover", help="Promove um usuário para administrador")
    p_prom.add_argument("email", help="E-mail a ser promovido")

    p_dem = subparsers.add_parser("rebaixar", help="Rebaixa um administrador para usuário comum")
    p_dem.add_argument("email", help="E-mail a ser rebaixado")

    p_conf = subparsers.add_parser(
        "confirmar-terceiro",
        help="Confirma o vínculo de conta Google com e-mail de terceiro",
    )
    p_conf.add_argument("email", help="E-mail com vínculo pendente")
    p_conf.add_argument("--sub", dest="sub", default=None, help="Google Sub esperado (opcional)")

    p_del = subparsers.add_parser("remover", help="Remove um usuário da lista")
    p_del.add_argument("email", help="E-mail a ser removido")

    args = parser.parse_args()

    if not args.comando:
        parser.print_help()
        sys.exit(0)

    if args.comando == "inicializar-proprietario":
        inicializar_proprietario(args.email)
    elif args.comando == "listar":
        listar_usuarios()
    elif args.comando == "autorizar":
        autorizar_usuario(args.email, args.role)
    elif args.comando == "bloquear":
        bloquear_usuario(args.email)
    elif args.comando == "reativar":
        reativar_usuario(args.email)
    elif args.comando == "promover":
        alterar_papel(args.email, "admin")
    elif args.comando == "rebaixar":
        alterar_papel(args.email, "user")
    elif args.comando == "confirmar-terceiro":
        confirmar_terceiro(args.email, args.sub)
    elif args.comando == "remover":
        remover_usuario(args.email)


if __name__ == "__main__":
    main()
