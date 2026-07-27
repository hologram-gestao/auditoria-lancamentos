"""sprint4: reconciliation_files (N arquivos por conciliação) + nova unicidade

Revision ID: b8e2d4a71f36
Revises: a3c7e1f95d24
Create Date: 2026-07-25 19:10:00.000000+00:00

Sprint 4 (BACK 04.2) — o hash MUDA DE NÍVEL:

  ANTES: `reconciliation_sessions.file_hash NOT NULL` +
         UNIQUE(client_id, omie_conta_id, reference_month, file_hash).
         Dois arquivos diferentes = duas sessões; fatura quebrada em 3 PDFs
         não consolidava num resumo só.

  DEPOIS: uma conciliação = conta + mês →
         UNIQUE(client_id, omie_conta_id, reference_month) (parcial, ativas);
         duplicata de ARQUIVO → UNIQUE(session_id, file_hash) em
         `reconciliation_files`.

Passos do upgrade (nesta ordem, todos dentro da transação do Alembic):
  1. Cria `reconciliation_files`.
  2. **Backfill sem perda**: 1 linha por sessão existente que tenha `file_hash`,
     preservando `created_at`/`updated_at` da sessão.
  3. Adiciona `reconciliation_file_entries.file_id` e aponta cada linha para a
     parte da sua sessão (é o que permite remover uma parte sem levar as outras).
  4. `reconciliation_sessions.file_hash` vira NULLABLE (mantida por histórico).
  5. **Pré-check bloqueante**: se já existirem 2+ sessões ATIVAS para a mesma
     (client, conta, mês), aborta com mensagem acionável. Criar o índice novo
     falharia de qualquer forma — abortar com instrução é melhor do que um
     `duplicate key` cru, e escolher qual sessão sobrevive é decisão de dado,
     não de migration (learnings: abortar em vez de "consertar" sozinho).
  6. Troca o índice único antigo pelo novo.

Downgrade: reverte o SCHEMA por completo (backfill de `file_hash` a partir da
primeira parte de cada sessão, drop de `file_id`, drop da tabela, índice antigo
de volta). ⚠️ Sessões com MAIS DE UMA parte não têm representação no modelo
antigo — o downgrade preserva a 1ª parte e as demais somem junto com a tabela.
Isso é inerente à contração do modelo, não um bug: documentado aqui para que
ninguém use o downgrade como rollback rotineiro depois de rodar multi-arquivo.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e2d4a71f36"
down_revision: str | None = "a3c7e1f95d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DUPLICATE_CHECK = sa.text("""
DO $$
DECLARE
    conflitos int;
BEGIN
    SELECT count(*) INTO conflitos FROM (
        SELECT 1
        FROM reconciliation_sessions
        WHERE deleted_at IS NULL
        GROUP BY client_id, omie_conta_id, reference_month
        HAVING count(*) > 1
    ) AS dup;

    IF conflitos > 0 THEN
        RAISE EXCEPTION
            'Migration b8e2d4a71f36 abortada: % combinacoes (cliente, conta, mes) tem mais de uma conciliacao ATIVA. A Sprint 4 exige uma conciliacao por conta+mes. Consolide ou descarte (soft-delete) as sessoes extras antes de migrar - a query de diagnostico esta no docstring da migration.',
            conflitos;
    END IF;
END $$;
""")


def upgrade() -> None:
    # 1. Tabela das partes.
    op.create_table(
        "reconciliation_files",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("filename_encrypted", sa.Text(), nullable=True),
        sa.Column("filename_iv", sa.String(length=24), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["reconciliation_sessions.id"],
            name="fk_reconciliation_files_session_id_reconciliation_sessions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reconciliation_files"),
    )
    op.create_index(
        "ix_reconciliation_files_session_id", "reconciliation_files", ["session_id"]
    )
    op.create_index(
        "uq_recon_files_session_hash",
        "reconciliation_files",
        ["session_id", "file_hash"],
        unique=True,
    )

    # 2. Backfill: 1 parte por sessão existente (inclusive as soft-deleted — a
    #    trilha histórica não pode perder o hash que já estava gravado).
    op.execute(
        sa.text("""
        INSERT INTO reconciliation_files
            (id, session_id, file_hash, status, created_at, updated_at)
        SELECT gen_random_uuid(), s.id, s.file_hash, 'parsed', s.created_at, s.updated_at
        FROM reconciliation_sessions AS s
        WHERE s.file_hash IS NOT NULL
        """)
    )

    # 3. Vínculo linha → parte.
    op.add_column(
        "reconciliation_file_entries", sa.Column("file_id", sa.UUID(), nullable=True)
    )
    op.create_index(
        "ix_reconciliation_file_entries_file_id", "reconciliation_file_entries", ["file_id"]
    )
    op.create_foreign_key(
        "fk_reconciliation_file_entries_file_id_reconciliation_files",
        "reconciliation_file_entries",
        "reconciliation_files",
        ["file_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.execute(
        sa.text("""
        UPDATE reconciliation_file_entries AS e
        SET file_id = f.id
        FROM reconciliation_files AS f
        WHERE f.session_id = e.session_id AND e.file_id IS NULL
        """)
    )

    # 4. `file_hash` da sessão vira legado nullable.
    op.alter_column(
        "reconciliation_sessions",
        "file_hash",
        existing_type=sa.String(length=64),
        nullable=True,
    )

    # 5. Pré-check + 6. troca dos índices.
    op.execute(_DUPLICATE_CHECK)
    op.drop_index("uq_recon_sessions_idempotency", table_name="reconciliation_sessions")
    op.create_index(
        "uq_recon_sessions_account_month",
        "reconciliation_sessions",
        ["client_id", "omie_conta_id", "reference_month"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    # Devolve o hash para a sessão a partir da parte mais antiga (ver ⚠️ no
    # docstring: partes extras não têm onde caber no modelo antigo).
    op.execute(
        sa.text("""
        UPDATE reconciliation_sessions AS s
        SET file_hash = f.file_hash
        FROM (
            SELECT DISTINCT ON (session_id) session_id, file_hash
            FROM reconciliation_files
            ORDER BY session_id, created_at, id
        ) AS f
        WHERE f.session_id = s.id AND s.file_hash IS NULL
        """)
    )
    # Sessão sem nenhuma parte (só possível se alguém apagou as linhas na mão):
    # sem hash não dá para restaurar o NOT NULL. Aborta em vez de inventar dado.
    op.execute(
        sa.text("""
        DO $$
        DECLARE
            orfas int;
        BEGIN
            SELECT count(*) INTO orfas
            FROM reconciliation_sessions WHERE file_hash IS NULL;
            IF orfas > 0 THEN
                RAISE EXCEPTION
                    'Downgrade b8e2d4a71f36 abortado: % sessoes sem nenhuma linha em reconciliation_files - nao ha hash para restaurar o NOT NULL.',
                    orfas;
            END IF;
        END $$;
        """)
    )

    op.drop_index("uq_recon_sessions_account_month", table_name="reconciliation_sessions")
    op.create_index(
        "uq_recon_sessions_idempotency",
        "reconciliation_sessions",
        ["client_id", "omie_conta_id", "reference_month", "file_hash"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.alter_column(
        "reconciliation_sessions",
        "file_hash",
        existing_type=sa.String(length=64),
        nullable=False,
    )

    op.drop_constraint(
        "fk_reconciliation_file_entries_file_id_reconciliation_files",
        "reconciliation_file_entries",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_reconciliation_file_entries_file_id", table_name="reconciliation_file_entries"
    )
    op.drop_column("reconciliation_file_entries", "file_id")

    op.drop_index("uq_recon_files_session_hash", table_name="reconciliation_files")
    op.drop_index("ix_reconciliation_files_session_id", table_name="reconciliation_files")
    op.drop_table("reconciliation_files")
