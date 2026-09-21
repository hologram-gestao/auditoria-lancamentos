"""Promove um usuário a `platform_admin` — o ÚNICO caminho para nascer plataforma.

Camada de organizações (épico 86e36ec0q, task 86e36ecqz, decisão Q3 do Pedro em
16/09/2026): `platform_admin` não entra em whitelist de API nenhuma. Quem vira
plataforma vira por este script, rodado por quem tem acesso ao banco — em dev
pelo `seed_dev.py`, em cloud pelo Cloud Run Job com as MESMAS secrets do
serviço.

O que faz, por e-mail:
    - `scope='system'` (admin ou manager de uma organização) → `scope='platform'`,
      `role='platform_admin'`, `organization_id=NULL`, `client_id=NULL`. É a
      única forma do CHECK `ck_users_scope_consistency` aceitar a linha.
    - Já plataforma → **no-op** (idempotente: a 2ª execução não muda nada e
      sai com sucesso).
    - `scope='client'` (usuário DO cliente) → **recusa**: usuário de tenant
      não vira administrador geral; se for o caso, é cadastro novo de staff.
    - E-mail inexistente → recusa.

O que NÃO faz:
    - Não mexe em `active` (um usuário desativado promovido continua
      desativado; avisa).
    - Não apaga `client_assignments` (plano §8.8: a plataforma alcança tudo
      pela matriz; as linhas de carteira que sobrarem só afetam a exibição
      "gerente responsável" — avisa a contagem). Conta só carteira de cliente
      ABERTO: a de cliente encerrado fica de propósito (§4.12, retenção) e não
      dá para mexer (toda escrita em encerrado é 409), então avisá-la seria
      pendência sem ação possível.
    - Não loga nem imprime nada além do e-mail e do resultado (§3.3).

⚠️ Promover cedo demais tranca a conta fora dos clientes: o front só ganha a
área da plataforma na onda 2 (plano §7, regra (d)). Em produção, só depois do
deploy do front.

Uso:
    cd apps/api
    uv run python -m scripts.promote_platform_admin --email pessoa@hologramgestao.com.br
    uv run python -m scripts.promote_platform_admin --email ... --dry-run

Sai com código 0 quando promoveu ou já era plataforma; 1 quando recusou.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

# Garante que ``apps/api/`` está no sys.path (idem seed_dev.py / rotate_encryption_key.py).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import func, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.models import Client, ClientAssignment, User, UserRole, UserScope  # noqa: E402
from app.db.session import close_db, get_session_factory, init_db  # noqa: E402

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = get_logger(__name__)


class PromotionOutcome(StrEnum):
    """Resultado fechado — é o que o log e o código de saída reportam."""

    PROMOTED = "promoted"
    ALREADY_PLATFORM = "already_platform"
    REFUSED_NOT_FOUND = "refused_not_found"
    REFUSED_CLIENT_SCOPE = "refused_client_scope"


@dataclass(frozen=True)
class PromotionResult:
    email: str
    outcome: PromotionOutcome
    #: Só no caminho de sucesso: a linha estava desativada (promovida mesmo assim).
    inactive: bool = False
    #: Linhas de carteira que ficaram (plano §8.8) — aviso, não erro.
    assignments_left: int = 0

    @property
    def ok(self) -> bool:
        return self.outcome in {PromotionOutcome.PROMOTED, PromotionOutcome.ALREADY_PLATFORM}


async def promote_platform_admin(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    email: str,
    dry_run: bool = False,
) -> PromotionResult:
    """Promove `email` a plataforma. Idempotente; recusa tenant e inexistente.

    A escrita é UMA transação: ou a linha vira plataforma inteira (scope, role,
    org e tenant nulos), ou nada muda — o CHECK do banco recusaria qualquer
    estado intermediário.
    """
    normalized = email.strip().lower()
    async with session_factory() as db:
        user = (await db.execute(select(User).where(User.email == normalized))).scalar_one_or_none()
        if user is None:
            return PromotionResult(email=normalized, outcome=PromotionOutcome.REFUSED_NOT_FOUND)
        if user.scope == UserScope.CLIENT.value:
            return PromotionResult(email=normalized, outcome=PromotionOutcome.REFUSED_CLIENT_SCOPE)
        # Só cliente ABERTO: a carteira de cliente encerrado é retida de
        # propósito (§4.12) e não aceita escrita (409) — não há o que fazer
        # com ela, então não é aviso.
        assignments_left = int(
            (
                await db.execute(
                    select(func.count(ClientAssignment.id))
                    .join(Client, Client.id == ClientAssignment.client_id)
                    .where(ClientAssignment.user_id == user.id, Client.closed_at.is_(None))
                )
            ).scalar_one()
        )
        if user.scope == UserScope.PLATFORM.value and user.role == UserRole.PLATFORM_ADMIN.value:
            return PromotionResult(
                email=normalized,
                outcome=PromotionOutcome.ALREADY_PLATFORM,
                inactive=not user.active,
                assignments_left=assignments_left,
            )

        if not dry_run:
            user.scope = UserScope.PLATFORM.value
            user.role = UserRole.PLATFORM_ADMIN.value
            # É um UPDATE: `None` vira NULL normalmente (a omissão por
            # `server_default` só existe no INSERT — ver o modelo, §4.8).
            user.organization_id = None
            user.client_id = None
            await db.commit()
        return PromotionResult(
            email=normalized,
            outcome=PromotionOutcome.PROMOTED,
            inactive=not user.active,
            assignments_left=assignments_left,
        )


def _report(result: PromotionResult, *, dry_run: bool) -> None:
    prefix = "[dry-run] " if dry_run else ""
    messages = {
        PromotionOutcome.PROMOTED: "promovido a platform_admin",
        PromotionOutcome.ALREADY_PLATFORM: "já é platform_admin (nada a fazer)",
        PromotionOutcome.REFUSED_NOT_FOUND: "RECUSADO: e-mail não encontrado",
        PromotionOutcome.REFUSED_CLIENT_SCOPE: (
            "RECUSADO: usuário DO CLIENTE não vira plataforma (cadastre um staff)"
        ),
    }
    print(f"{prefix}{result.email}: {messages[result.outcome]}")
    if result.inactive:
        print("  aviso: a conta está DESATIVADA — continua desativada.")
    if result.assignments_left:
        print(
            f"  aviso: {result.assignments_left} linha(s) de carteira em cliente ABERTO "
            "ficaram (a plataforma alcança tudo pela matriz; só afeta a exibição de "
            "responsável)."
        )
    log.info(
        "platform_admin_promotion",
        outcome=result.outcome.value,
        dry_run=dry_run,
        assignments_left=result.assignments_left,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--email", required=True, help="E-mail do usuário a promover.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Mostra o que faria, sem gravar nada."
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    setup_logging(settings)
    init_db(settings)
    try:
        result = await promote_platform_admin(
            session_factory=get_session_factory(), email=args.email, dry_run=args.dry_run
        )
    finally:
        await close_db()
    _report(result, dry_run=args.dry_run)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
