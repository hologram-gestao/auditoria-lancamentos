"""remove tipos de anomalia sem detector (Sprint 2 / BACK 02.5)

Revision ID: a3d5e1c9f7b2
Revises: f2a7c9d31b84
Create Date: 2026-07-13 12:30:00.000000+00:00

"Schema sem lógica é uma promessa que o produto não cumpre." O catálogo tinha
6 tipos que NENHUM código gerava (admin/UI os via, mas nenhum detector os
emitia). Sem requisito que os peça, o padrão é REMOVER (BACK 02.5). Restam só
os tipos COM detector: 2 estruturais (missing_in_*) + 4 de qualificação (S19).

Mudança:
  - DELETE dos 6 tipos órfãos de bancos já semeados. Defensivo: só apaga os
    que NÃO estão referenciados por nenhuma `reconciliation_anomalies` (não
    deveriam estar — nunca foram gerados — mas não apagamos histórico se
    alguém tiver criado uma referência à mão).

Compat / reversibilidade:
  - Idempotente: DELETE-if-exists; `seed_dev.py` já não recria os 6.
  - Downgrade re-insere os 6 (id novo via gen_random_uuid; a chave é `code`),
    com `ON CONFLICT (code) DO NOTHING` — round-trip seguro.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a3d5e1c9f7b2"
down_revision: str | None = "f2a7c9d31b84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORPHAN_CODES = (
    "wrong_account",
    "inconsistent_category",
    "category_mismatch_nature",
    "internal_transfer_as_revenue",
    "possible_duplicate",
    "classification_improvement",
)

# (code, name, severity, description) — dados originais do seed, para o downgrade.
_ORPHAN_SEED = [
    (
        "wrong_account",
        "Lançamento possivelmente na conta errada",
        "critical",
        "Suspeita de que o lançamento foi associado a uma conta bancária "
        "diferente da que aparece no extrato fornecido.",
    ),
    (
        "inconsistent_category",
        "Mesma descrição, categorias diferentes entre meses",
        "moderate",
        "Padrões de descrição idênticos em meses diferentes mas com "
        "categorias financeiras divergentes — sugere erro de classificação.",
    ),
    (
        "category_mismatch_nature",
        "Categoria incompatível com a natureza do lançamento",
        "moderate",
        "Categoria de despesa marcada em lançamento de receita (ou vice-versa). "
        "Pode indicar erro de cadastro no Omie.",
    ),
    (
        "internal_transfer_as_revenue",
        "Transferência interna classificada como receita",
        "critical",
        "Movimento entre contas do mesmo cliente classificado como receita — "
        "infla artificialmente o resultado e distorce relatórios contábeis.",
    ),
    (
        "possible_duplicate",
        "Possível lançamento duplicado",
        "moderate",
        "Dois ou mais lançamentos no Omie com mesmo valor, fornecedor e data "
        "próxima — pode indicar duplicação.",
    ),
    (
        "classification_improvement",
        "Sugestão de padronização de categoria",
        "info",
        "Lançamento com categoria genérica (ex: 'Outras despesas') que poderia "
        "ser refinada para uma categoria mais específica do plano de contas.",
    ),
]


def upgrade() -> None:
    # `_ORPHAN_CODES` são constantes literais deste módulo (não input externo);
    # o f-string é seguro. `# noqa: S608` documenta a exceção consciente.
    codes_csv = ", ".join(f"'{c}'" for c in _ORPHAN_CODES)
    op.execute(  # noqa: S608 — SQL montado de constantes literais, sem input externo
        f"""
        DELETE FROM anomaly_types
        WHERE code IN ({codes_csv})
          AND id NOT IN (
            SELECT anomaly_type_id FROM reconciliation_anomalies
            WHERE anomaly_type_id IS NOT NULL
          )
        """
    )


def downgrade() -> None:
    for code, name, severity, description in _ORPHAN_SEED:
        safe_name = name.replace("'", "''")
        safe_desc = description.replace("'", "''")
        op.execute(  # noqa: S608 — SQL montado de constantes literais deste módulo
            f"""
            INSERT INTO anomaly_types (id, code, name, description, severity, active)
            VALUES (gen_random_uuid(), '{code}', '{safe_name}', '{safe_desc}', '{severity}', true)
            ON CONFLICT (code) DO NOTHING
            """
        )
