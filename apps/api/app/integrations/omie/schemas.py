"""DTOs Pydantic dos endpoints Omie consumidos pelo sistema.

Padrão de campos:
    - O Omie usa camelCase nas chaves (`nCodCC`, `cNatureza`) e nomes em
      português abreviado. Mantemos essas chaves como `alias` mas expomos
      atributos snake_case Pythônicos.
    - Datas vêm como `DD/MM/YYYY` (string) — convertidas para `date` via validator.
    - Valores monetários vêm como número absoluto (sempre positivo);
      `cNatureza` indica o sinal (`'D'` débito, `'C'` crédito) — exposto
      como propriedade `signed_amount`.

Referência: `Docs/documentation/6. Integração com API do Omie-*.md`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OmieAccountType(StrEnum):
    """Valores do campo `tipo` em ListarContasCorrentes."""

    CHECKING = "CC"  # Conta Corrente
    CREDIT_CARD = "CA"  # Cartão de Crédito


class OmieEntryNatureza(StrEnum):
    """Valores do campo `cNatureza` em ListarExtrato."""

    DEBITO = "D"  # saída → valor negativo
    CREDITO = "C"  # entrada → valor positivo


class OmieEntryStatus(StrEnum):
    """Valores do campo `cSituacao` em ListarExtrato (canônico do DB).

    A doc oficial declara `cSituacao` como `string40` sem enumerar; estes
    são os valores conhecidos em prática. Valores fora do enum não quebram
    o parsing (o campo é `str`) — apenas não disparam regras de anomalia.
    """

    CONCILIADO = "Conciliado"
    ATRASADO = "Atrasado"
    PREVISTO = "Previsto"


class OmieTituloStatus(StrEnum):
    """Valores aceitos pelo parâmetro `filtrar_por_status` em
    `ListarContasPagar` / `ListarContasReceber`.

    O Omie documenta (em `ListarContasPagar`):
        CANCELADO, PAGO, LIQUIDADO, EMABERTO, PAGTO_PARCIAL, VENCEHOJE,
        AVENCER, ATRASADO

    Para o matching nosso interesse é em títulos **ainda não conciliados** —
    usamos `ATRASADO` (vencidos) + `AVENCER` (com vencimento futuro). NÃO
    usar `"PREVISTO"` aqui: a Omie devolve 5001 (caso real em prod 19/05/2026)
    porque esse valor não existe no enum oficial. O campo `status_titulo`
    no response **pode** vir como "Previsto" em camelCase — não confundir.
    """

    ATRASADO = "ATRASADO"
    AVENCER = "AVENCER"


def _parse_brazilian_date(v: str | date | None) -> date | None:
    """Converte `DD/MM/YYYY` (string Omie) para `date`. None passa direto."""
    if v is None or isinstance(v, date):
        return v
    if isinstance(v, str) and v:
        try:
            return datetime.strptime(v, "%d/%m/%Y").date()
        except ValueError as exc:
            raise ValueError(f"Data Omie inválida: {v!r} (esperado DD/MM/YYYY)") from exc
    return None


# ----------------------------------------------------------------------
# ListarContasCorrentes
# ----------------------------------------------------------------------


class ContaCorrente(BaseModel):
    """Item do array `ListarContasCorrentes` retornado pelo endpoint homônimo.

    Doc §6.2 — usado para popular o cache L1 (`omie_accounts_cache`) por cliente.

    Os nomes dos campos seguem o que a API do Omie devolve de fato (ver
    https://app.omie.com.br/api/v1/geral/contacorrente/). A doc interna v1
    do projeto descrevia `nCodBanco`/`descricaoBanco`/`tipo`, que NÃO existem
    nesse endpoint — o Omie devolve `codigo_banco` (string) e
    `tipo_conta_corrente`, e não devolve o nome do banco por extenso aqui.
    """

    n_cod_cc: int = Field(alias="nCodCC", description="ID único no Omie.")
    descricao: str = Field(description="Nome da conta (ex: 'Sicredi 91263-1').")
    codigo_banco: str | None = Field(
        default=None,
        alias="codigo_banco",
        description="Código de 3 dígitos do banco (ex: '748' Sicredi, '341' Itaú).",
    )
    tipo: str = Field(
        alias="tipo_conta_corrente",
        description="'CC' (corrente), 'CA' (cartão), 'CX' (caixinha), etc.",
    )

    model_config = ConfigDict(populate_by_name=True)


# ----------------------------------------------------------------------
# ListarExtrato
# ----------------------------------------------------------------------


class LancamentoExtrato(BaseModel):
    """Item de `listaMovimentos` retornado por `ListarExtrato`.

    Os nomes seguem o response real do Omie (ver
    https://app.omie.com.br/api/v1/financas/extrato/). A v1 deste schema
    usava `nCodLanc`, `dDtLanc`, `nValorLanc`, `cDescrLanc`, `cCateg`,
    `cFornecedor`, `cStatus` — TODOS errados; em prod, `model_validate`
    falharia (campos com default ficavam None silenciosamente, os
    obrigatórios estouravam ValidationError). Caso documentado no
    `Docs/AUDITORIA_OMIE_INTEGRACAO.md` CRÍTICO-1 / CRÍTICO-2.

    Estratégia: os atributos refletem o alias Omie literal, mas expomos
    properties (`description`, `supplier`, `category`) com a escolha
    consensual entre os pares disponíveis (`cRazCliente` x `cDesCliente`,
    `cDesCategoria` x `cCodCategoria`), pra que `lancamento_cache` e
    consumers fiquem isolados dessa decisão.
    """

    n_cod_lancamento: int = Field(alias="nCodLancamento", description="ID único do lançamento.")
    n_cod_lanc_relac: int | None = Field(
        default=None,
        alias="nCodLancRelac",
        description=(
            "ID do lançamento relacionado (parcelamento). Não usado no "
            "matching atual; persiste no cache pra ser exercitado depois."
        ),
    )
    c_natureza: str = Field(alias="cNatureza", description="'D' (débito) ou 'C' (crédito).")
    d_data_lancamento: date = Field(alias="dDataLancamento", description="Data do lançamento.")
    n_valor_documento: Decimal = Field(alias="nValorDocumento", description="Valor absoluto.")
    c_situacao: str = Field(
        alias="cSituacao",
        description="Status: 'Conciliado', 'Atrasado', 'Previsto' (string40 na doc).",
    )
    c_observacoes: str = Field(
        default="",
        alias="cObservacoes",
        description="Texto livre — usado como descrição na tela de revisão.",
    )
    c_cod_categoria: str | None = Field(
        default=None,
        alias="cCodCategoria",
        description="Código da categoria (ex: 'DT').",
    )
    c_des_categoria: str | None = Field(
        default=None,
        alias="cDesCategoria",
        description="Descrição da categoria (ex: 'Despesas com IOF').",
    )
    c_raz_cliente: str | None = Field(
        default=None,
        alias="cRazCliente",
        description="Razão social do cliente/fornecedor.",
    )
    c_des_cliente: str | None = Field(
        default=None,
        alias="cDesCliente",
        description="Nome fantasia do cliente/fornecedor.",
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("d_data_lancamento", mode="before")
    @classmethod
    def _parse_date(cls, v: str | date | None) -> date | None:
        return _parse_brazilian_date(v)

    @property
    def signed_amount(self) -> Decimal:
        """Valor com sinal: débito → negativo, crédito → positivo."""
        if self.c_natureza == OmieEntryNatureza.DEBITO.value:
            return -self.n_valor_documento
        return self.n_valor_documento

    @property
    def description(self) -> str:
        """Texto humano do lançamento — usa `cObservacoes`."""
        return self.c_observacoes or ""

    @property
    def supplier(self) -> str | None:
        """Cliente/fornecedor: razão social preferida, fallback nome fantasia."""
        return self.c_raz_cliente or self.c_des_cliente

    @property
    def category(self) -> str | None:
        """Categoria: descrição preferida, fallback código."""
        return self.c_des_categoria or self.c_cod_categoria


# ----------------------------------------------------------------------
# ListarContasPagar / ListarContasReceber
# ----------------------------------------------------------------------


class TituloAPagarReceber(BaseModel):
    """Item de `cadastro` em ListarContasPagar e ListarContasReceber.

    Estrutura idêntica nos dois endpoints (Doc §6.2).
    """

    codigo_lancamento_omie: int = Field(description="ID do título no Omie.")
    data_vencimento: date = Field(description="Data de vencimento.")
    valor_documento: Decimal = Field(description="Valor do título.")
    nome_fornecedor: str | None = Field(default=None)
    descricao_categoria: str | None = Field(default=None)
    status_titulo: str = Field(
        description=(
            "Status do título devolvido pelo Omie. A doc oficial não enumera "
            "os valores; em prática observamos camelCase (ex: 'Atrasado', "
            "'Previsto', 'Liquidado'). Tratado como str livre para não "
            "explodir em valor não previsto."
        )
    )

    @field_validator("data_vencimento", mode="before")
    @classmethod
    def _parse_date(cls, v: str | date | None) -> date | None:
        return _parse_brazilian_date(v)


# ----------------------------------------------------------------------
# Resposta de erro (faultstring)
# ----------------------------------------------------------------------


class OmieFaultPayload(BaseModel):
    """Estrutura de erro retornada com HTTP 200 — particularidade da API Omie.

    Toda resposta deve ser checada por `faultstring` ANTES de processar dados.
    """

    fault_string: str | None = Field(default=None, alias="faultstring")
    fault_code: str | None = Field(default=None, alias="faultcode")

    model_config = ConfigDict(populate_by_name=True)
