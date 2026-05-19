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
    """Valores do campo `cStatus` em ListarExtrato."""

    CONCILIADO = "Conciliado"
    ATRASADO = "Atrasado"
    PREVISTO = "Previsto"


class OmieTituloStatus(StrEnum):
    """Valores do `status_titulo` em ListarContasPagar/Receber."""

    ATRASADO = "ATRASADO"
    PREVISTO = "PREVISTO"


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
    """Item de `extrato` retornado por `ListarExtrato`.

    O valor é absoluto; use `signed_amount` para obter o valor com sinal
    aplicado por `c_natureza`.
    """

    n_cod_lanc: int = Field(alias="nCodLanc", description="ID único do lançamento.")
    c_natureza: str = Field(alias="cNatureza", description="'D' (débito) ou 'C' (crédito).")
    d_dt_lanc: date = Field(alias="dDtLanc", description="Data do lançamento.")
    n_valor_lanc: Decimal = Field(alias="nValorLanc", description="Valor absoluto.")
    c_descr_lanc: str = Field(alias="cDescrLanc", default="")
    c_categ: str | None = Field(default=None, alias="cCateg")
    c_fornecedor: str | None = Field(default=None, alias="cFornecedor")
    c_status: str = Field(alias="cStatus")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("d_dt_lanc", mode="before")
    @classmethod
    def _parse_date(cls, v: str | date | None) -> date | None:
        return _parse_brazilian_date(v)

    @property
    def signed_amount(self) -> Decimal:
        """Valor com sinal: débito → negativo, crédito → positivo."""
        if self.c_natureza == OmieEntryNatureza.DEBITO.value:
            return -self.n_valor_lanc
        return self.n_valor_lanc


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
    status_titulo: str = Field(description="'ATRASADO' ou 'PREVISTO'.")

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
