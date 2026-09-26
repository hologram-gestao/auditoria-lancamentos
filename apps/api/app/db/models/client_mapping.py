"""O DE-PARA do cliente: decisões com vigência e materializações imutáveis (Sprint 12, BACK 12.3 — R2/R4/R5).

**A chave é `(cliente, tipo de origem, categoria, destino)` → alvo**, única por
competência de início da vigência. A MESMA categoria alimenta destinos diferentes
ao mesmo tempo (a transferência entre contas de mesma titularidade é `nao_mapear` no
demonstrativo e alvo real no fluxo de caixa) — é a tese da sprint.

**`source_type` é o TIPO do provedor, nunca FK de conexão.** Conexão é removida e
recriada ao trocar credencial; o de-para é ativo do CLIENTE e não pode ir junto.

**Três estados, e um deles não é linha:** `alvo` e `nao_mapear` são decisões
PERSISTIDAS (`decision_type`); "sem decisão" é a AUSÊNCIA de linha vigente. O CHECK
`decision_target_coherent` garante que `alvo` tem `target_id` e `nao_mapear` não.
"Sem categoria de origem" nem chega aqui: é o movimento com `category_code` nulo.

**Vigência append-only por competência (R4).** Alterar uma decisão é INSERIR outra
linha com `effective_from` posterior; a anterior vale até o mês anterior. Nunca
`UPDATE` de decisão — por isso a tabela não tem `updated_at`.

**Materialização imutável (R5).** `(cliente, destino, competência, versão)` único;
reaplicar cria versão N+1. Os ITENS guardam SNAPSHOT de códigos e valores, sem FK
para `client_movements` nem para a decisão: o encerramento purga a base e as
decisões (configuração), e a materialização — "o que aconteceu" — FICA intacta.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Date as SQLDate
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import UUIDPrimaryKeyMixin
from app.db.models.client_movement import (
    MAX_MOVEMENT_CATEGORY_CODE_CHARS,
    MAX_MOVEMENT_REF_CHARS,
    MAX_MOVEMENT_SOURCE_ID_CHARS,
    MAX_MOVEMENT_SOURCE_TYPE_CHARS,
)
from app.db.models.mapping_catalog import MAX_DESTINATION_TYPE_CHARS, MAX_TARGET_CODE_CHARS


class DecisionType(StrEnum):
    """O que a decisão diz — fonte ÚNICA do CHECK `decision_type`."""

    #: Aponta um alvo do catálogo do destino.
    ALVO = "alvo"
    #: Decisão EXPLÍCITA de não levar a categoria a este destino. É decisão tomada,
    #: distinta de "sem decisão" (pendência) — conta no numerador da cobertura.
    NAO_MAPEAR = "nao_mapear"


class DecisionOrigin(StrEnum):
    """De onde a decisão veio — fonte ÚNICA do CHECK `origin` (R7)."""

    #: Pré-preenchida do plano de contas sincronizado (só `demonstrativo_contabil`).
    HERDADA = "herdada"
    #: Tomada ou confirmada por uma pessoa.
    CONFIRMADA = "confirmada"


class MaterializedSituation(StrEnum):
    """A situação de UM movimento na aplicação — fonte ÚNICA do CHECK dos itens.

    As QUATRO situações da prévia (R5). `sem_categoria` fica fora do denominador
    da cobertura (R3); os outros três formam o denominador.
    """

    ALVO = "alvo"
    NAO_MAPEAR = "nao_mapear"
    SEM_DECISAO = "sem_decisao"
    SEM_CATEGORIA = "sem_categoria"


def _in_check(column: str, enum: type[StrEnum]) -> str:
    valores = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({valores})"


def decision_type_check() -> str:
    """Predicado do CHECK de `decision_type` — a MESMA string vai na migration."""
    return _in_check("decision_type", DecisionType)


def decision_origin_check() -> str:
    """Predicado do CHECK de `origin` — a MESMA string vai na migration."""
    return _in_check("origin", DecisionOrigin)


def materialized_situation_check() -> str:
    """Predicado do CHECK de `situation` dos itens — a MESMA string vai na migration."""
    return _in_check("situation", MaterializedSituation)


#: `alvo` exige o alvo; `nao_mapear` o proíbe. Garantido no BANCO, não só no serviço.
DECISION_TARGET_COHERENT_CHECK = (
    "(decision_type = 'alvo' AND target_id IS NOT NULL) "
    "OR (decision_type = 'nao_mapear' AND target_id IS NULL)"
)
#: O item com situação `alvo` carrega o código do alvo; os outros três, não.
ITEM_TARGET_COHERENT_CHECK = (
    "(situation = 'alvo' AND target_code IS NOT NULL) "
    "OR (situation <> 'alvo' AND target_code IS NULL)"
)
#: Competência/vigência sempre no dia 1 — mesmo CHECK de `client_movements`.
DECISION_EFFECTIVE_FROM_CHECK = "EXTRACT(DAY FROM effective_from) = 1"
MATERIALIZATION_COMPETENCE_CHECK = "EXTRACT(DAY FROM competence) = 1"
MATERIALIZATION_VERSION_CHECK = "version >= 1"

#: Nomes curtos de propósito: o Postgres trunca identificador acima de 63
#: caracteres (e o `alembic --sql` recusa) — o nome "descritivo" desta chave tinha 72.
UQ_CLIENT_MAPPING_DECISION = "uq_client_mapping_decisions_key_effective_from"
IX_CLIENT_MAPPING_DECISION_CLIENT_DESTINATION = (
    "ix_client_mapping_decisions_client_id_destination_id"
)
UQ_CLIENT_MAPPING_MATERIALIZATION = "uq_client_mapping_materializations_version"
IX_MATERIALIZATION_ITEM_MATERIALIZATION = (
    "ix_client_mapping_materialization_items_materialization_id"
)
#: FKs cujo nome pela convenção passaria de 63 caracteres — nome EXPLÍCITO.
FK_MATERIALIZATION_DESTINATION = "fk_client_mapping_materializations_destination_id"
FK_MATERIALIZATION_ITEM_MATERIALIZATION = (
    "fk_client_mapping_materialization_items_materialization_id"
)

#: Rótulos dos CHECKs (a `NAMING_CONVENTION` prefixa `ck_<tabela>_`).
DECISION_TYPE_CK_LABEL = "decision_type"
DECISION_ORIGIN_CK_LABEL = "origin"
DECISION_TARGET_CK_LABEL = "decision_target_coherent"
DECISION_EFFECTIVE_FROM_CK_LABEL = "effective_from_first_day"
MATERIALIZATION_COMPETENCE_CK_LABEL = "competence_first_day"
MATERIALIZATION_VERSION_CK_LABEL = "version_positive"
ITEM_SITUATION_CK_LABEL = "situation"
ITEM_TARGET_CK_LABEL = "item_target_coherent"

DECISION_TARGET_CONSTRAINT = f"ck_client_mapping_decisions_{DECISION_TARGET_CK_LABEL}"


class ClientMappingDecision(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "client_mapping_decisions"

    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "source_type",
            "category_code",
            "destination_id",
            "effective_from",
            name=UQ_CLIENT_MAPPING_DECISION,
        ),
        Index(IX_CLIENT_MAPPING_DECISION_CLIENT_DESTINATION, "client_id", "destination_id"),
        CheckConstraint(text(decision_type_check()), name=DECISION_TYPE_CK_LABEL),
        CheckConstraint(text(decision_origin_check()), name=DECISION_ORIGIN_CK_LABEL),
        CheckConstraint(text(DECISION_TARGET_COHERENT_CHECK), name=DECISION_TARGET_CK_LABEL),
        CheckConstraint(text(DECISION_EFFECTIVE_FROM_CHECK), name=DECISION_EFFECTIVE_FROM_CK_LABEL),
    )

    #: CASCADE: a exclusão definitiva leva tudo. O ENCERRAMENTO purga as decisões
    #: explicitamente (`close_client_purge`) — são CONFIGURAÇÃO do cliente.
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    #: TIPO do provedor (`omie`, `arquivo`…) — nunca FK de conexão.
    source_type: Mapped[str] = mapped_column(String(MAX_MOVEMENT_SOURCE_TYPE_CHARS), nullable=False)
    #: CÓDIGO da categoria na origem. O nome nunca persiste (§4.5).
    category_code: Mapped[str] = mapped_column(
        String(MAX_MOVEMENT_CATEGORY_CODE_CHARS), nullable=False
    )
    #: RESTRICT: destino com decisão não some.
    destination_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mapping_destinations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Valor de `DecisionType`, travado por CHECK.
    decision_type: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Obrigatório se `alvo`, nulo se `nao_mapear` (CHECK). RESTRICT: alvo
    #: referenciado não se apaga — desativa (R1).
    target_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mapping_targets.id", ondelete="RESTRICT"),
        nullable=True,
        default=None,
    )
    #: Valor de `DecisionOrigin`, travado por CHECK.
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Competência (dia 1) em que esta vigência COMEÇA. Vale até a véspera da
    #: próxima linha da mesma chave.
    effective_from: Mapped[date] = mapped_column(SQLDate, nullable=False)
    #: Quem gravou. RESTRICT: usuário não se apaga com decisão pendurada (a
    #: exclusão definitiva do cliente apaga as decisões ANTES dos usuários do tenant).
    author_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<ClientMappingDecision client={self.client_id} category={self.category_code!r} "
            f"destination={self.destination_id} type={self.decision_type!r} "
            f"from={self.effective_from}>"
        )


class ClientMappingMaterialization(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "client_mapping_materializations"

    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "destination_id",
            "competence",
            "version",
            name=UQ_CLIENT_MAPPING_MATERIALIZATION,
        ),
        CheckConstraint(
            text(MATERIALIZATION_COMPETENCE_CHECK), name=MATERIALIZATION_COMPETENCE_CK_LABEL
        ),
        CheckConstraint(text(MATERIALIZATION_VERSION_CHECK), name=MATERIALIZATION_VERSION_CK_LABEL),
    )

    #: CASCADE só na exclusão DEFINITIVA. No encerramento a linha de `clients`
    #: fica, então a materialização fica também — é "o que aconteceu" (§4.12).
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    destination_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "mapping_destinations.id", ondelete="RESTRICT", name=FK_MATERIALIZATION_DESTINATION
        ),
        nullable=False,
    )
    #: Snapshot do TIPO do destino no momento da materialização — a Sprint 13 lê
    #: daqui sem depender do catálogo continuar igual.
    destination_type: Mapped[str] = mapped_column(
        String(MAX_DESTINATION_TYPE_CHARS), nullable=False
    )
    competence: Mapped[date] = mapped_column(SQLDate, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Hash da ENTRADA (base de movimentos presentes + decisões vigentes) que a
    #: prévia confirmada viu. É o que prova que o registro corresponde à prévia.
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: As vigências usadas: `[{decision_id, category_code, effective_from, decision_type,
    #: target_code}]`. JSONB porque é registro histórico, não chave de consulta.
    decisions_used: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    # Totais das QUATRO situações, calculados uma vez pela função pura (12.6).
    mapped_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    mapped_count: Mapped[int] = mapped_column(Integer, nullable=False)
    not_mapped_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    not_mapped_count: Mapped[int] = mapped_column(Integer, nullable=False)
    undecided_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    undecided_count: Mapped[int] = mapped_column(Integer, nullable=False)
    uncategorized_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    uncategorized_count: Mapped[int] = mapped_column(Integer, nullable=False)
    undecided_categories: Mapped[int] = mapped_column(Integer, nullable=False)

    #: `True` = havia valor sem decisão e alguém confirmou seguir com cobertura
    #: parcial. Quem (`author_id`), quando (`created_at`) e quanto
    #: (`undecided_amount`) estão NESTE registro imutável — é a trilha (R5).
    partial_coverage_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    author_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ClientMappingMaterializationItem(UUIDPrimaryKeyMixin, Base):
    """Um movimento na materialização — SNAPSHOT, sem FK para o movimento."""

    __tablename__ = "client_mapping_materialization_items"

    __table_args__ = (
        Index(IX_MATERIALIZATION_ITEM_MATERIALIZATION, "materialization_id"),
        CheckConstraint(text(materialized_situation_check()), name=ITEM_SITUATION_CK_LABEL),
        CheckConstraint(text(ITEM_TARGET_COHERENT_CHECK), name=ITEM_TARGET_CK_LABEL),
    )

    materialization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "client_mapping_materializations.id",
            ondelete="CASCADE",
            name=FK_MATERIALIZATION_ITEM_MATERIALIZATION,
        ),
        nullable=False,
    )
    #: Desnormalizado de propósito: toda query filtra por tenant (§3.15) sem
    #: depender de um JOIN que alguém pode esquecer.
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(MAX_MOVEMENT_SOURCE_TYPE_CHARS), nullable=False)
    source_movement_id: Mapped[str] = mapped_column(
        String(MAX_MOVEMENT_SOURCE_ID_CHARS), nullable=False
    )
    source_account_id: Mapped[str | None] = mapped_column(
        String(MAX_MOVEMENT_REF_CHARS), nullable=True, default=None
    )
    movement_date: Mapped[date] = mapped_column(SQLDate, nullable=False)
    #: COM SINAL, como na base. `Numeric(14,2)`.
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    category_code: Mapped[str | None] = mapped_column(
        String(MAX_MOVEMENT_CATEGORY_CODE_CHARS), nullable=True, default=None
    )
    situation: Mapped[str] = mapped_column(String(20), nullable=False)
    #: CÓDIGO do alvo (snapshot) quando a situação é `alvo`. O nome é resolvido na
    #: geração do arquivo (S13), nunca guardado aqui.
    target_code: Mapped[str | None] = mapped_column(
        String(MAX_TARGET_CODE_CHARS), nullable=True, default=None
    )
    #: A vigência que decidiu este movimento (nula em `sem_decisao`/`sem_categoria`).
    decision_effective_from: Mapped[date | None] = mapped_column(
        SQLDate, nullable=True, default=None
    )
