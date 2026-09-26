"""Catálogo de DESTINOS e ALVOS do de-para, por organização (Sprint 12, BACK 12.3 — R1).

**A ponta da seta.** O de-para traduz uma categoria do cliente para um ALVO dentro de
um DESTINO (demonstrativo contábil, fluxo de caixa…). Sem catálogo, o alvo viraria
texto livre e a Sprint 13 geraria arquivo contábil a partir de string digitada —
por isso o alvo é **código de catálogo**, e toda decisão referencia um alvo que
existe (FK, não texto).

**Por ORGANIZAÇÃO, não por cliente.** A controladoria e o contador parceiro querem
estruturas de demonstração diferentes, e é essa diferença que justifica a sprint; os
clientes de uma organização compartilham a estrutura dela.

**O tipo de destino é SLUG validado, não enum de banco nem CHECK dos cinco.** Os
cinco tipos nascem seedados (`DEFAULT_DESTINATION_TYPES`), mas acrescentar um sexto é
CADASTRO, não migração (PRD, "Suposições"). O CHECK que existe é de FORMATO
(minúsculas, dígitos e `_`) — é ele que garante que o slug cabe no evento
`depara_aplicado` sem que o sink recuse a métrica.

**Um destino por (organização, tipo)** — `UNIQUE(organization_id, destination_type)`
(decisão do planejador, ADR-074-BE). **Código de alvo único por destino** —
`UNIQUE(destination_id, code)`. O código não se edita (é a chave pela qual a
importação da 12.5 casa e pela qual a materialização guarda snapshot); o nome sim.

**O nome do alvo é dado da ORGANIZAÇÃO** (ex.: "Receita Bruta de Vendas" do plano de
demonstração do escritório), não do cliente final — ele pode persistir em claro. O
nome da CATEGORIA do cliente continua fora do disco (§4.5).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

#: Os cinco tipos que as três frentes declararam (PRD, R1), com o nome de exibição
#: inicial. Seedados em toda organização existente (migration) e em toda organização
#: nova (`OrganizationService.create_organization`). NÃO é o vocabulário fechado dos
#: tipos — um sexto entra por cadastro.
DEFAULT_DESTINATION_TYPES: tuple[tuple[str, str], ...] = (
    ("demonstrativo_gerencial", "Demonstrativo gerencial"),
    ("demonstrativo_contabil", "Demonstrativo contábil"),
    ("conta_contabil", "Conta contábil"),
    ("natureza_fiscal", "Natureza fiscal"),
    ("fluxo_de_caixa", "Fluxo de caixa"),
)

#: O ÚNICO tipo que herda do plano de contas sincronizado (R7): a conta de
#: demonstrativo que a origem declara (`dre_code`) alimenta este destino e nenhum
#: outro. Os outros quatro abrem sem decisão.
INHERITING_DESTINATION_TYPE = "demonstrativo_contabil"

MAX_DESTINATION_TYPE_CHARS = 60
MAX_DESTINATION_NAME_CHARS = 120
MAX_TARGET_CODE_CHARS = 50
MAX_TARGET_NAME_CHARS = 200

#: Formato do slug do tipo — a MESMA regra que o sink de métrica aplica ao
#: `destino` de `depara_aplicado` (`usage_events.schemas.DESTINO_SLUG_PATTERN`) e que
#: a borda HTTP valida. POSIX (`~`) no CHECK, Python `re` na borda.
DESTINATION_TYPE_PATTERN = r"^[a-z][a-z0-9_]{0,59}$"
DESTINATION_TYPE_CHECK = "destination_type ~ '^[a-z][a-z0-9_]{0,59}$'"

UQ_MAPPING_DESTINATION_ORG_TYPE = "uq_mapping_destinations_organization_id_destination_type"
UQ_MAPPING_TARGET_DESTINATION_CODE = "uq_mapping_targets_destination_id_code"

DESTINATION_TYPE_CK_LABEL = "destination_type_slug"
DESTINATION_TYPE_CONSTRAINT = f"ck_mapping_destinations_{DESTINATION_TYPE_CK_LABEL}"


class MappingDestination(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "mapping_destinations"

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "destination_type", name=UQ_MAPPING_DESTINATION_ORG_TYPE
        ),
        CheckConstraint(text(DESTINATION_TYPE_CHECK), name=DESTINATION_TYPE_CK_LABEL),
    )

    #: RESTRICT, como `client_categories`: organização não se apaga (suspende).
    #: Sem índice próprio — a UNIQUE serve toda busca por organização pelo prefixo.
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    destination_type: Mapped[str] = mapped_column(
        String(MAX_DESTINATION_TYPE_CHARS), nullable=False
    )
    name: Mapped[str] = mapped_column(String(MAX_DESTINATION_NAME_CHARS), nullable=False)
    #: `False` = destino desligado: não recebe decisão nova nem aplicação (409
    #: nomeando o destino). As decisões e materializações que já existem ficam.
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    def __repr__(self) -> str:
        return (
            f"<MappingDestination org={self.organization_id} type={self.destination_type!r} "
            f"active={self.active}>"
        )


class MappingTarget(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "mapping_targets"

    __table_args__ = (
        UniqueConstraint("destination_id", "code", name=UQ_MAPPING_TARGET_DESTINATION_CODE),
    )

    #: CASCADE: alvo não vive sem o destino. Na prática nunca dispara — destino não
    #: tem rota de exclusão, e alvo referenciado por decisão é RESTRICT do outro lado.
    destination_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mapping_destinations.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Código do catálogo — imutável depois de criado (ver o docstring do módulo).
    code: Mapped[str] = mapped_column(String(MAX_TARGET_CODE_CHARS), nullable=False)
    name: Mapped[str] = mapped_column(String(MAX_TARGET_NAME_CHARS), nullable=False)
    #: `False` = desativado: não recebe decisão NOVA (422), mas as que já apontam
    #: para ele continuam. Desativar é permitido; apagar alvo referenciado, não.
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    def __repr__(self) -> str:
        return f"<MappingTarget destination={self.destination_id} code={self.code!r}>"
