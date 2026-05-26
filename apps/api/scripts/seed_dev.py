"""Seeds para desenvolvimento — popula admin inicial + catálogo de anomalias.

Idempotente: pode ser rodado múltiplas vezes sem duplicar dados (usa upsert
por chave única — `email` para users, `code` para anomaly_types).

Uso:
    cd apps/api
    uv run python scripts/seed-dev.py

Senha do admin é configurável via env var `SEED_ADMIN_PASSWORD`. Default é
um valor de dev óbvio que NUNCA deve ser usado em prod (nem mesmo staging).

Seed do catálogo segue Doc §0 §anomaly_types — 8 tipos pré-cadastrados.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

# Garante que `apps/api/` está no sys.path quando rodado como `python scripts/seed_dev.py`.
# Sem isso, `sys.path[0]` é `scripts/` e o `import app.*` falha. Idempotente.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import select  # noqa: E402  (sys.path setado acima é pré-requisito)
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

# psycopg async não suporta ProactorEventLoop (default Windows)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.models import (  # noqa: E402
    AnomalySeverity,
    AnomalyType,
    User,
    UserRole,
)
from app.db.session import close_db, get_session_factory, init_db  # noqa: E402

DEFAULT_ADMIN_EMAIL = "admin@hologram.com.br"
DEFAULT_ADMIN_NAME = "Admin Dev"
DEFAULT_ADMIN_PASSWORD = "ChangeMeIn1stLogin!"  # noqa: S105

# Seed canônico (Doc §0 §anomaly_types §seed inicial)
ANOMALY_TYPES_SEED: list[dict[str, Any]] = [
    {
        "code": "missing_in_omie",
        "name": "Movimentação sem lançamento no Omie",
        "severity": AnomalySeverity.CRITICAL,
        "description": (
            "Linha presente no extrato/fatura do banco que não tem lançamento "
            "correspondente no Omie. Indica receita ou despesa não registrada."
        ),
    },
    {
        "code": "missing_in_file",
        "name": "Lançamento Omie sem correspondente no extrato",
        "severity": AnomalySeverity.CRITICAL,
        "description": (
            "Lançamento Omie com status Atrasado que deveria já ter sido pago/recebido "
            "mas não aparece no extrato. Pode indicar título lançado errado ou pagamento perdido."
        ),
    },
    {
        "code": "wrong_account",
        "name": "Lançamento possivelmente na conta errada",
        "severity": AnomalySeverity.CRITICAL,
        "description": (
            "Suspeita de que o lançamento foi associado a uma conta bancária "
            "diferente da que aparece no extrato fornecido."
        ),
    },
    {
        "code": "inconsistent_category",
        "name": "Mesma descrição, categorias diferentes entre meses",
        "severity": AnomalySeverity.MODERATE,
        "description": (
            "Padrões de descrição idênticos em meses diferentes mas com "
            "categorias financeiras divergentes — sugere erro de classificação."
        ),
    },
    {
        "code": "category_mismatch_nature",
        "name": "Categoria incompatível com a natureza do lançamento",
        "severity": AnomalySeverity.MODERATE,
        "description": (
            "Categoria de despesa marcada em lançamento de receita (ou vice-versa). "
            "Pode indicar erro de cadastro no Omie."
        ),
    },
    {
        "code": "internal_transfer_as_revenue",
        "name": "Transferência interna classificada como receita",
        "severity": AnomalySeverity.CRITICAL,
        "description": (
            "Movimento entre contas do mesmo cliente classificado como receita — "
            "infla artificialmente o resultado e distorce relatórios contábeis."
        ),
    },
    {
        "code": "possible_duplicate",
        "name": "Possível lançamento duplicado",
        "severity": AnomalySeverity.MODERATE,
        "description": (
            "Dois ou mais lançamentos no Omie com mesmo valor, fornecedor e data "
            "próxima — pode indicar duplicação."
        ),
    },
    {
        "code": "classification_improvement",
        "name": "Sugestão de padronização de categoria",
        "severity": AnomalySeverity.INFO,
        "description": (
            "Lançamento com categoria genérica (ex: 'Outras despesas') que poderia "
            "ser refinada para uma categoria mais específica do plano de contas."
        ),
    },
    # S19 — Qualificação inteligente de lançamentos (BACK 12.1).
    {
        "code": "qualificacao_suspeita",
        "name": "Qualificação suspeita (IA)",
        "severity": AnomalySeverity.MODERATE,
        "description": (
            "Categoria ou fornecedor do Omie pode estar incoerente com a descrição "
            "do extrato (análise IA, confiança média)."
        ),
    },
    {
        "code": "qualificacao_incoerente",
        "name": "Qualificação incoerente (IA)",
        "severity": AnomalySeverity.CRITICAL,
        "description": (
            "Categoria ou fornecedor do Omie diverge claramente da descrição do "
            "extrato (análise IA, alta confiança)."
        ),
    },
    {
        "code": "padrao_quebrado",
        "name": "Padrão histórico quebrado",
        "severity": AnomalySeverity.INFO,
        "description": (
            "Categoria atual difere da mais frequente para este fornecedor nas "
            "últimas 3 conciliações do cliente."
        ),
    },
    {
        "code": "valor_outlier",
        "name": "Valor fora do padrão",
        "severity": AnomalySeverity.INFO,
        "description": (
            "Valor da movimentação está fora do padrão (>3 desvios-padrão) histórico "
            "para este fornecedor (amostra >= 5 conciliações)."
        ),
    },
]


async def seed_admin(session: AsyncSession) -> None:
    """Cria 1 admin inicial se ainda não existir."""
    email = os.getenv("SEED_ADMIN_EMAIL", DEFAULT_ADMIN_EMAIL).lower()
    name = os.getenv("SEED_ADMIN_NAME", DEFAULT_ADMIN_NAME)
    password = os.getenv("SEED_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)

    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        print(f"[seed] admin já existe: {email}")
        return

    admin = User(
        name=name,
        email=email,
        password_hash=hash_password(password),
        role=UserRole.ADMIN.value,
        active=True,
    )
    session.add(admin)
    await session.flush()
    print(f"[seed] admin criado: {email}  (senha: {password})")


async def seed_anomaly_types(session: AsyncSession) -> None:
    """Insere/atualiza catálogo canônico de tipos de anomalia.

    Idempotente por `code`: novos tipos são adicionados sem duplicar os
    pré-existentes. Versão original tinha 8 tipos; S19 (BACK 12.1)
    adicionou +4 (qualificacao_suspeita, qualificacao_incoerente,
    padrao_quebrado, valor_outlier).
    """
    inserted = 0
    skipped = 0
    for item in ANOMALY_TYPES_SEED:
        existing = await session.scalar(select(AnomalyType).where(AnomalyType.code == item["code"]))
        if existing is not None:
            skipped += 1
            continue
        session.add(
            AnomalyType(
                code=item["code"],
                name=item["name"],
                description=item["description"],
                severity=item["severity"].value,
                active=True,
            )
        )
        inserted += 1
    await session.flush()
    print(f"[seed] anomaly_types: {inserted} inseridos, {skipped} já existiam")


async def main() -> None:
    settings = get_settings()
    init_db(settings)
    try:
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await seed_admin(session)
            await seed_anomaly_types(session)
    finally:
        await close_db()
    print("[seed] concluído.")


if __name__ == "__main__":
    asyncio.run(main())
