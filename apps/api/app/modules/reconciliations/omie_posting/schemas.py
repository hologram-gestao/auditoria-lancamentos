"""Contrato do lote de lançamento no Omie (Sprint 7 / BACK 07.4).

Entrada rígida, saída simples (memória `feedback_pydantic_strict_input_lenient_output`):
o request valida UUID, tamanho e teto de lote; a resposta usa tipos simples e
**códigos categóricos** — nunca texto livre — para que a UI possa ramificar sem
casar string em português.
"""

from __future__ import annotations

from datetime import date as _date
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.cnpj import normalize_cnpj
from app.db.models import COD_INT_LANC_MAX_LENGTH
from app.integrations.omie.schemas import OMIE_CODE_MAX

#: `cCodCateg` é `string20` na Omie (mesmo teto do `cCodIntLanc`).
COD_CATEGORIA_MAX_LENGTH = COD_INT_LANC_MAX_LENGTH
#: `cCodDep` é `string40` na Omie (`DepartamentoOmie.codigo`).
COD_DEPARTAMENTO_MAX_LENGTH = 40
#: `razao_social` do `IncluirCliente` é `string60`; `email`, `string500`.
RAZAO_SOCIAL_MAX_LENGTH = 60
EMAIL_MAX_LENGTH = 500
#: Tetos de itens por linha. Folgados para o uso real (uma compra dividida em
#: IOF + categoria; uma PCTAX com poucos departamentos) e baixos o bastante para
#: o título não virar uma lista que ninguém confere.
MAX_CATEGORIAS_POR_LINHA = 10
MAX_DEPARTAMENTOS_POR_LINHA = 30
#: Tolerância das somas (valor das categorias contra o da compra, percentuais dos
#: departamentos contra 100): a MESMA do cruzamento (§5.1), um centavo.
SOMA_TOLERANCIA = Decimal("0.01")

#: Como a pessoa pediu para lançar a linha.
PostingMode = Literal["provisionar", "direto"]

#: Desfecho de cada linha do lote. Fechado — a UI ramifica nestes três.
PostingLineStatus = Literal["lancada", "bloqueada", "erro"]

#: Motivos, **categóricos**. Existem para a UI decidir o que oferecer ao
#: operador (reclassificar? conferir no Omie? tentar de novo?) sem interpretar
#: a frase em português, que pode mudar.
PostingLineReason = Literal[
    # --- bloqueios (nada foi enviado ao Omie) ---
    "linha_inexistente",
    "nao_e_sem_omie",
    "linha_ignorada",
    "ja_lancada",
    "envio_anterior_sem_confirmacao",
    "chave_em_conflito",
    "lancamento_ja_vinculado",
    # Estorno (valor positivo): a representação do crédito no contrato real
    # do IncluirLancCC segue não-verificada (S-1) — bloqueado até lá.
    "estorno_nao_verificado",
    # --- 86e3n70q0 (bloco B): bloqueios do roteamento e da provisão ---
    # Lançamento direto pedido com `OMIE_POSTING_ENABLED` desligado.
    "direto_desligado",
    # Provisão pedida com `OMIE_PAYABLE_POSTING_ENABLED` desligado.
    "provisao_desligada",
    # Lançamento direto num processo "vencimento da fatura": o `IncluirLancCC`
    # grava a data da COMPRA, que esse processo não usa. Provisionar funciona.
    "direto_indisponivel_no_modo_vencimento",
    # Sem CNPJ achado, sem dados para cadastrar e sem fornecedor padrão.
    "fornecedor_pendente",
    # O cliente exige rateio por departamento e a linha não trouxe nenhum.
    "departamento_pendente",
    # A soma das categorias não fecha com o valor da compra (tolerância 0,01).
    "categorias_nao_fecham",
    # A linha já tem intenção registrada no OUTRO modo, e trocar poderia lançar
    # a mesma compra duas vezes.
    "modo_divergente",
    # --- 86e3qhhtb: parcelas futuras de compra parcelada ---
    # Parcelas futuras pedidas com `OMIE_INSTALLMENT_POSTING_ENABLED` desligado.
    "parcelas_futuras_desligadas",
    # Parcelas futuras pedidas numa linha que não é parcela `N/M` com `N < M`.
    "sem_parcelas_futuras",
    # A parcela desta linha já foi registrada por uma fatura anterior, mas o título
    # dela ainda não foi confirmado: criar outro aqui poderia duplicar.
    "parcela_pendente_na_fatura_anterior",
    # --- erros (houve tentativa) ---
    "erro_omie",
    # O título da compra entrou e alguma parcela futura não: a linha retoma delas.
    "parcelas_incompletas",
    "omie_indisponivel",
    # Erro 757 da Omie: a competência da data está fechada no Painel do Contador.
    "competencia_fechada",
    # --- sucesso por reconciliação (não houve novo POST) ---
    "reconciliada",
]

#: Desfecho de UMA parcela futura (86e3qhhtb).
#: - `provisionada`: título criado (neste envio ou num anterior);
#: - `ja_existia`: a parcela já está no Omie por OUTRA linha (a série foi criada
#:   numa fatura anterior, ou a mesma compra foi provisionada noutra conciliação):
#:   nada foi criado;
#: - `pendente`: envios sem confirmação no teto; conferir no Omie pela chave;
#: - `erro`: a Omie recusou (mensagem verbatim).
InstallmentResultStatus = Literal["provisionada", "ja_existia", "pendente", "erro"]

#: Em que forma a linha foi (ou está indo) ao Omie, espelho de `OmiePostingKind`.
PostingKindValue = Literal["direto", "provisao"]
#: Próximo passo da provisão, espelho de `OmiePostingStep`.
PostingStepValue = Literal["fornecedor", "titulo", "baixa", "concluido"]


class CategoriaRateio(BaseModel):
    """Uma parcela da compra numa categoria (subtask 10 nascendo aqui).

    `valor` positivo, em reais, com no máximo 2 casas. A soma das parcelas é
    conferida contra o valor da compra no SERVIÇO (tolerância 0,01), porque é lá
    que a linha é conhecida.
    """

    codigo: str = Field(min_length=1, max_length=COD_CATEGORIA_MAX_LENGTH)
    valor: Decimal = Field(gt=0, max_digits=14, decimal_places=2)

    @field_validator("codigo", mode="after")
    @classmethod
    def _strip(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("codigo da categoria não pode ser vazio")
        return stripped


class DepartamentoRateio(BaseModel):
    """Uma parcela do rateio por departamento, em percentual (2 casas)."""

    codigo: str = Field(min_length=1, max_length=COD_DEPARTAMENTO_MAX_LENGTH)
    percentual: Decimal = Field(gt=0, le=100, max_digits=5, decimal_places=2)

    @field_validator("codigo", mode="after")
    @classmethod
    def _strip(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("codigo do departamento não pode ser vazio")
        return stripped


class OmiePostingLineRequest(BaseModel):
    """Uma linha da fatura a lançar, com a forma e a classificação escolhidas.

    86e3n70q0 (bloco B): a pessoa escolhe POR LINHA entre `provisionar` (título no
    Contas a Pagar; o padrão) e `direto` (`IncluirLancCC`, o lançamento da Sprint
    7). Os campos do fornecedor, do rateio e das categorias só existem na
    provisão: mandá-los num `direto` é 400, para nenhum dado ser ignorado em
    silêncio. CNPJ, razão social e e-mail trafegam em memória até o Omie e não
    persistem (§4.5).
    """

    file_entry_id: UUID
    modo: PostingMode = Field(
        "provisionar",
        description="`provisionar` (Contas a Pagar, padrão) ou `direto` (IncluirLancCC).",
    )
    cod_categoria: str | None = Field(
        None,
        min_length=1,
        max_length=COD_CATEGORIA_MAX_LENGTH,
        description=(
            "`cCodCateg` do Omie. Obrigatório no `direto`; na provisão, obrigatório "
            "quando `categorias` não vier."
        ),
    )
    categorias: list[CategoriaRateio] | None = Field(
        None,
        min_length=1,
        max_length=MAX_CATEGORIAS_POR_LINHA,
        description=(
            "Só na provisão: a compra dividida entre categorias (`{codigo, valor}`). "
            "A soma fecha com o valor da compra (0,01). Exclui `cod_categoria`."
        ),
    )
    cnpj_fornecedor: str | None = Field(
        None,
        min_length=14,
        max_length=18,
        description="Só na provisão: CNPJ do fornecedor (14 dígitos, com ou sem máscara).",
    )
    codigo_fornecedor: int | None = Field(
        None,
        ge=1,
        le=OMIE_CODE_MAX,
        description=(
            "Só na provisão: o `codigo_cliente_omie` do cadastro que a pessoa escolheu "
            "(busca pelo nome, sugestão ou busca por CNPJ). O título vai para ESTE "
            "cadastro, sem nova busca, inclusive fornecedor do exterior sem CNPJ. "
            "Exclui `cnpj_fornecedor`, `razao_social` e `email`."
        ),
    )
    razao_social: str | None = Field(
        None,
        min_length=1,
        max_length=RAZAO_SOCIAL_MAX_LENGTH,
        description=(
            "Só na provisão, junto com `email`: cadastra o fornecedor quando o CNPJ "
            "não tem cadastro no Omie."
        ),
    )
    email: str | None = Field(
        None,
        min_length=3,
        max_length=EMAIL_MAX_LENGTH,
        description="Só na provisão, junto com `razao_social` (a Omie exige e-mail).",
    )
    departamentos: list[DepartamentoRateio] | None = Field(
        None,
        min_length=1,
        max_length=MAX_DEPARTAMENTOS_POR_LINHA,
        description=(
            "Só na provisão: rateio por departamento (`{codigo, percentual}`), "
            "percentuais somando 100. Obrigatório se o cliente exige departamento."
        ),
    )
    parcelas_futuras: bool | None = Field(
        None,
        description=(
            "Só na provisão (86e3qhhtb): numa compra parcelada `N/M` com `N < M`, "
            "provisiona também os títulos das parcelas `N+1` a `M`, com o mesmo "
            "fornecedor, categorias e rateio. A parcela é lida pelo SERVIDOR, nunca "
            "do pedido."
        ),
    )

    @field_validator("cod_categoria", mode="after")
    @classmethod
    def _strip_and_require(cls, v: str | None) -> str | None:
        """Categoria em branco é bloqueio, não lançamento vazio.

        Espaço em branco passaria pelo `min_length=1` e chegaria à Omie como
        categoria inexistente — um erro do fornecedor por uma causa nossa.
        """
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            raise ValueError("cod_categoria não pode ser vazio")
        return stripped

    @field_validator("cnpj_fornecedor", mode="after")
    @classmethod
    def _cnpj_valido(cls, v: str | None) -> str | None:
        """Normalizado para os 14 dígitos (a mensagem do erro não ecoa o valor)."""
        return None if v is None else normalize_cnpj(v)

    @field_validator("razao_social", "email", mode="after")
    @classmethod
    def _strip_texto(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            raise ValueError("texto vazio")
        return stripped

    @field_validator("email", mode="after")
    @classmethod
    def _email_plausivel(cls, v: str | None) -> str | None:
        """Só a forma mínima: quem valida o e-mail de verdade é a Omie (`1040`)."""
        if v is not None and ("@" not in v or v.startswith("@") or v.endswith("@")):
            raise ValueError("e-mail inválido")
        return v

    @model_validator(mode="after")
    def _coerente_com_o_modo(self) -> Self:
        provisao_only = {
            "categorias": self.categorias,
            "cnpj_fornecedor": self.cnpj_fornecedor,
            "codigo_fornecedor": self.codigo_fornecedor,
            "razao_social": self.razao_social,
            "email": self.email,
            "departamentos": self.departamentos,
            # `false` explícito num `direto` não é dado ignorado: é a ausência do pedido.
            "parcelas_futuras": self.parcelas_futuras or None,
        }
        if self.modo == "direto":
            if self.cod_categoria is None:
                raise ValueError("cod_categoria é obrigatório no lançamento direto")
            if any(v is not None for v in provisao_only.values()):
                raise ValueError("fornecedor, rateio e categorias só existem na provisão")
            return self
        if (self.cod_categoria is None) == (self.categorias is None):
            raise ValueError("informe cod_categoria OU categorias")
        if (self.razao_social is None) != (self.email is None):
            raise ValueError("razao_social e email vêm juntos")
        if self.razao_social is not None and self.cnpj_fornecedor is None:
            raise ValueError("cadastrar fornecedor exige o CNPJ")
        if self.codigo_fornecedor is not None and self.cnpj_fornecedor is not None:
            raise ValueError("informe codigo_fornecedor OU cnpj_fornecedor")
        if self.categorias is not None:
            codigos = [c.codigo for c in self.categorias]
            if len(set(codigos)) != len(codigos):
                raise ValueError("categoria repetida")
        if self.departamentos is not None:
            codigos = [d.codigo for d in self.departamentos]
            if len(set(codigos)) != len(codigos):
                raise ValueError("departamento repetido")
            total = sum((d.percentual for d in self.departamentos), Decimal(0))
            if abs(total - Decimal(100)) > SOMA_TOLERANCIA:
                raise ValueError("os percentuais dos departamentos somam 100")
        return self


class OmiePostingBatchRequest(BaseModel):
    """Body do POST /api/v1/reconciliations/{session_id}/omie-postings.

    O teto de lote **não** está aqui: ele vive no `Settings`
    (`OMIE_POSTING_MAX_BATCH`) e é validado no service, para poder ser ajustado
    por ambiente sem deploy. `max_length` fixo no schema daria um 422 com um
    número que ninguém consegue mudar.
    """

    lines: list[OmiePostingLineRequest] = Field(
        min_length=1,
        description="Linhas selecionadas. Duplicatas do mesmo file_entry_id são recusadas.",
    )

    @field_validator("lines", mode="after")
    @classmethod
    def _no_duplicate_entries(cls, v: list[OmiePostingLineRequest]) -> list[OmiePostingLineRequest]:
        """A mesma linha duas vezes no lote seria pedir dois lançamentos dela.

        A dedup do banco impediria o segundo, mas o operador receberia um
        "bloqueada: já lançada" confuso para uma linha que ele mandou uma vez
        só. Recusar na entrada é mais honesto.
        """
        seen = {line.file_entry_id for line in v}
        if len(seen) != len(v):
            raise ValueError("file_entry_id repetido no lote")
        return v


class OmiePostingInstallmentResult(BaseModel):
    """Uma parcela futura de compra parcelada: datas, valor e o que aconteceu com ela."""

    numero: int
    total: int
    vencimento: _date
    emissao: _date
    valor: Decimal
    status: InstallmentResultStatus
    payable_id: int | None = None
    #: Em `erro`, a mensagem VERBATIM do provedor; em `pendente`, onde conferir.
    message: str | None = None


class OmiePostingLineResult(BaseModel):
    """Desfecho de UMA linha. É o que a tela mostra ao lado dela."""

    file_entry_id: UUID
    status: PostingLineStatus
    reason: PostingLineReason | None = None
    #: Mensagem em PT-BR para o operador. Em `erro_omie` é a mensagem VERBATIM
    #: do provedor (é o que o torna acionável); nunca contém credencial nem
    #: stack, e nunca é logada (ADR-023-BE).
    message: str | None = None
    omie_lancamento_id: int | None = None
    #: 86e3n70q0 (bloco B). Em que forma a linha foi ao Omie e, na provisão, em
    #: que passo PAROU e com quais ids: sucesso parcial (título criado, baixa
    #: recusada) é estado legítimo, e a tela precisa dizer onde parou. `step` é o
    #: PRÓXIMO passo a fazer; `concluido` quando terminou.
    kind: PostingKindValue | None = None
    step: PostingStepValue | None = None
    supplier_code: int | None = None
    payable_id: int | None = None
    payment_id: int | None = None
    #: 86e3qhhtb. As parcelas futuras da compra, quando pedidas ou já existentes;
    #: `None` quando a linha não tem nenhuma.
    parcelas: list[OmiePostingInstallmentResult] | None = None


class OmiePostingBatchPayload(BaseModel):
    """Conteúdo do envelope `{data}` — resumo por linha + agregados."""

    lines: list[OmiePostingLineResult]
    lancadas: int
    bloqueadas: int
    com_erro: int


class OmiePostingBatchResponse(BaseModel):
    """Response do POST /reconciliations/{session_id}/omie-postings."""

    data: OmiePostingBatchPayload
