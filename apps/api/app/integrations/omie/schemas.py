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

import html
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import get_args

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


def unescape_omie_text(value: str) -> str:
    """Desfaz entidades HTML que o Omie devolve em campos de texto livre.

    Caso real (dev, 02/09/2026, task 86e33bmkb): `cDesCliente` veio
    "Transf. Itaú Unibanco &gt;&gt; Sicoob" e a UI exibiu o `&gt;` cru —
    React escapa na renderização, então a entidade nunca vira `>` sozinha.
    Aplicado nas properties de exibição (description/supplier/category),
    nunca nos campos brutos: quem compara/mapeia códigos continua vendo o
    byte que o Omie mandou.
    """
    return html.unescape(value)


class OmieAccountType(StrEnum):
    """Códigos de `tipo_conta_corrente` em ListarContasCorrentes (Omie).

    Enum **não-exaustivo** — só os tipos relevantes pro matching do MVP.
    Doc oficial (https://app.omie.com.br/api/v1/geral/contacorrente/)
    declara 13 valores possíveis (`AC, AD, CA, CC, CE, CG, CN, CP, CR,
    CV, CX, MT, PG`); o campo `tipo: str` no schema aceita qualquer
    valor — o enum aqui só nomeia os que tratamos com lógica especial.

    **Atenção semântica** (auditoria M-1, corrigido em 20/05/2026):
    a v1 deste enum mapeava `CREDIT_CARD = "CA"`, mas na Omie:
      - `CA` = **Conta Aplicação** (investimento, não cartão!)
      - `CR` = **Cartão de Crédito**
    Bugs decorrentes: front renderizava `CA` como "Cartão" e classifica-
    ria Conta Aplicação como cartão de crédito, com consequências em
    badges e filtros de UI.
    """

    CHECKING = "CC"  # Conta Corrente
    CREDIT_CARD = "CR"  # Cartão de Crédito
    INVESTMENT = "CA"  # Conta Aplicação


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
        description=(
            "Código de 2 letras. Valores possíveis (doc oficial): "
            "'CC' Conta Corrente, 'CR' Cartão de Crédito, 'CA' Conta "
            "Aplicação, 'CP' Poupança, 'CX' Caixinha, e mais 8 (`AC`, "
            "`AD`, `CE`, `CG`, `CN`, `CV`, `MT`, `PG`)."
        ),
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
            "ID do lançamento relacionado (parcelamento). NÃO decide match "
            "(§5) e NÃO é persistido em lugar nenhum — só trafega até o "
            "matcher para a medição de pagamento dividido, que conta quantas "
            "linhas fechariam por SOMA de lançamentos. A descrição anterior "
            "dizia que ele persistia no cache; era falso, e levava a acreditar "
            "que o dado estava no banco."
        ),
    )
    c_natureza: str = Field(
        alias="cNatureza",
        description=(
            "'D' (débito) ou 'C' (crédito) na doc. ⚠️ Fixture real de conta "
            "CARTÃO (21/08/2026): vem 'P' (pagamento) / 'R' (recebimento), com "
            "`nValorDocumento` JÁ SINALIZADO (P negativo, R positivo) — ver "
            "`signed_amount`, que cobre as duas convenções."
        ),
    )
    d_data_lancamento: date = Field(alias="dDataLancamento", description="Data do lançamento.")
    n_valor_documento: Decimal = Field(alias="nValorDocumento", description="Valor absoluto.")
    c_situacao: str | None = Field(
        default=None,
        alias="cSituacao",
        description=(
            "Status: 'Conciliado', 'Atrasado', 'Previsto' (string40 na doc). "
            "⚠️ Opcional por evidência real (21/08/2026): lançamento "
            "recém-criado via `IncluirLancCC` volta no extrato SEM a chave "
            "`cSituacao` (readback da captura) — exigi-la derrubaria o "
            "processamento de qualquer extrato relido no dia de uma inclusão."
        ),
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
    c_cod_int_lanc: str | None = Field(
        default=None,
        alias="cCodIntLanc",
        description=(
            "Chave de integração do lançamento. ⚠️ **VERIFICADO em 21/08/2026: "
            "o `ListarExtrato` NÃO devolve este campo** — nem em linha "
            "orgânica, nem na recém-criada pela captura (readback). "
            "Consequência: a reconciliação pós-timeout da BACK 07.4 é sempre "
            "INCONCLUSIVA por este caminho, e o ADL **não reenvia** (nunca "
            "duplica). A saída provada pela captura é outra: o `IncluirLancCC` "
            "é IDEMPOTENTE sobre `cCodIntLanc` (2º POST devolveu o MESMO "
            "`nCodLanc`, status 0) — mudar o caminho de timeout para reenviar "
            "é decisão registrada em aberto, não implementada."
        ),
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("d_data_lancamento", mode="before")
    @classmethod
    def _parse_date(cls, v: str | date | None) -> date | None:
        return _parse_brazilian_date(v)

    @property
    def signed_amount(self) -> Decimal:
        """Valor com sinal: débito → negativo, crédito → positivo.

        Cobre DUAS convenções reais (fixture de 21/08/2026):
        - Conta corrente (doc): natureza 'D'/'C' com valor ABSOLUTO — o 'D'
          é invertido aqui.
        - Cartão (observado): natureza 'P'/'R' com valor JÁ SINALIZADO
          (P negativo, R positivo) — cai no `return` direto, sem inverter.
        Inverter qualquer coisa além de 'D' quebraria o cartão.
        """
        if self.c_natureza == OmieEntryNatureza.DEBITO.value:
            return -self.n_valor_documento
        return self.n_valor_documento

    @property
    def description(self) -> str:
        """Texto humano do lançamento — usa `cObservacoes` (entidades HTML desfeitas)."""
        return unescape_omie_text(self.c_observacoes) if self.c_observacoes else ""

    @property
    def supplier(self) -> str | None:
        """Cliente/fornecedor: razão social preferida, fallback nome fantasia.

        Entidades HTML desfeitas — o Omie devolve `&gt;` e afins em texto livre.
        """
        raw = self.c_raz_cliente or self.c_des_cliente
        return unescape_omie_text(raw) if raw else None

    @property
    def category(self) -> str | None:
        """Categoria: descrição preferida (entidades HTML desfeitas), fallback código."""
        if self.c_des_categoria:
            return unescape_omie_text(self.c_des_categoria)
        return self.c_cod_categoria


# ----------------------------------------------------------------------
# ListarContasPagar / ListarContasReceber
# ----------------------------------------------------------------------


class TituloAPagarReceber(BaseModel):
    """Item de `conta_pagar_cadastro` / `conta_receber_cadastro` em
    ListarContasPagar / ListarContasReceber.

    Estrutura idêntica nos dois endpoints. Os nomes seguem o response real
    do Omie (ver
    https://app.omie.com.br/api/v1/financas/contapagar/ e .../contareceber/).

    A v1 deste schema declarava `nome_fornecedor` e `descricao_categoria`,
    que NÃO existem no response oficial — Pydantic deixava-os como `None`
    silenciosamente (defaults), e a tela de revisão de títulos ficaria com
    "—" pra tudo. Caso documentado no `Docs/AUDITORIA_OMIE_INTEGRACAO.md`
    CRÍTICO-5.

    O Omie devolve apenas códigos (`codigo_cliente_fornecedor`,
    `codigo_categoria`) — resolver pra nome legível exige chamadas
    adicionais a `ListarClientes`/`ListarFornecedores`/`ListarCategorias`,
    o que fica para uma sessão dedicada (vai precisar de cache batch).
    Por enquanto a tela de revisão exibe o código quando for renderizar.
    """

    codigo_lancamento_omie: int = Field(description="ID do título no Omie.")
    data_vencimento: date = Field(description="Data de vencimento.")
    valor_documento: Decimal = Field(description="Valor do título.")
    codigo_cliente_fornecedor: int | None = Field(
        default=None,
        description=(
            "ID do cliente/fornecedor no Omie. Para resolver o nome legível, "
            "consultar `ListarClientes` (campo `codigo_cliente_omie`) — não "
            "implementado ainda."
        ),
    )
    codigo_categoria: str | None = Field(
        default=None,
        description=(
            "Código da categoria (ex: 'DT'). Doc oficial não devolve a "
            "descrição neste endpoint; resolver via `ListarCategorias` futuro."
        ),
    )
    numero_documento: str | None = Field(
        default=None,
        description="Número do documento (ex: '00123/A') — útil pra rastreio na revisão.",
    )
    observacao: str | None = Field(
        default=None,
        description="Texto livre — útil pra exibir contexto na tela de revisão.",
    )
    status_titulo: str = Field(
        default="",
        description=(
            "`string3` na doc oficial — valores não enumerados (`'PAG'`, "
            "`'ATR'`, etc.). Tratado como str livre. Auditoria A-2 pendente "
            "(precisa fixture real pra confirmar formato)."
        ),
    )

    @field_validator("data_vencimento", mode="before")
    @classmethod
    def _parse_date(cls, v: str | date | None) -> date | None:
        return _parse_brazilian_date(v)


# ----------------------------------------------------------------------
# ListarCategorias (Sprint 7 / BACK 07.3)
# ----------------------------------------------------------------------


class CategoriaOmie(BaseModel):
    """Item de `categoria_cadastro` em `ListarCategorias` (`geral/categorias/`).

    ⚠️ **NOMES DE CAMPO NÃO-VERIFICADOS contra uma resposta real.** Vêm da doc
    oficial (https://app.omie.com.br/api/v1/geral/categorias/), não de uma
    chamada gravada — a mesma situação que já quebrou em produção duas vezes
    neste repositório (`ListarExtrato` e `ListarContasCorrentes`, ver
    `LancamentoExtrato` e `ContaCorrente`). O `scripts/capture_omie_fixtures.py`
    já captura este endpoint; assim que a fixture existir,
    `tests/unit/test_omie_fixtures.py` valida este DTO contra ela e FALHA na
    divergência.

    **O que é fato:** o campo que o lançamento precisa se chama `cCodCateg` no
    `IncluirLancCC` (BACK 07.1) e aparece como `codigo_categoria` no
    `ListarContasPagar/Receber` (`TituloAPagarReceber`) — a Omie usa nomes
    diferentes para o mesmo dado em endpoints diferentes, o que é justamente o
    motivo de não deduzir o nome "por analogia".

    Só dois campos são declarados: é tudo o que o combobox de classificação
    precisa. Declarar campos que não usamos aumentaria a superfície de
    divergência sem ganho nenhum.
    """

    codigo: str = Field(
        alias="codigo",
        description="Código da categoria — é o valor que vai em `cCodCateg`.",
    )
    descricao: str = Field(
        alias="descricao",
        description="Descrição legível da categoria (ex.: 'Despesas com IOF').",
    )
    conta_inativa: str | None = Field(
        default=None,
        alias="conta_inativa",
        description=(
            "'S'/'N' na doc. Categoria inativa não deve ser oferecida para "
            "classificação. Opcional: se o nome real for outro, o filtro "
            "simplesmente não remove nada — nunca some categoria por engano."
        ),
    )

    model_config = ConfigDict(populate_by_name=True)

    @property
    def is_active(self) -> bool:
        """`False` só quando a Omie diz explicitamente que a conta é inativa.

        O default é INCLUIR. Um nome de campo divergente faria o filtro sumir
        com o catálogo inteiro do cliente — falhar para o lado de mostrar
        demais é recuperável; sumir com as categorias trava o lançamento.
        """
        return (self.conta_inativa or "N").strip().upper() != "S"


# ----------------------------------------------------------------------
# IncluirLancCC (ESCRITA — Sprint 7 / BACK 07.1)
# ----------------------------------------------------------------------
#
# ⚠️ **CONTRATO PARCIALMENTE VERIFICADO (S-1 ainda aberta).** A FORMA aninhada
# (`cCodIntLanc` no topo + `cabecalho`/`detalhes`) deixou de ser suposição em
# 21/08/2026: o formato PLANO anterior foi enviado à API real e RECUSADO com
# `5001 - Tag [CCODCATEG] não faz parte da estrutura do tipo complexo
# [lanccIncluirRequest]` (fixture `incluir_lanc_cc.response.json` da captura),
# exatamente como a doc oficial (https://developer.omie.com.br/service-list/,
# serviço `financas/contacorrentelancamentos/`) descrevia. O que SEGUE
# não-verificado até uma resposta de ACEITE: os nomes internos de `cabecalho`/
# `detalhes`, a obrigatoriedade de cada campo e a representação do sinal
# débito/crédito (não há `cNatureza` na escrita — ver o docstring do request).
#
# O gate que fecha a lacuna é `tests/unit/test_omie_fixtures.py`, que roda
# estes DTOs contra a fixture gravada por `scripts/capture_omie_fixtures.py`
# (opt-in de escrita). Enquanto a fixture de ACEITE não existir, o teste não
# passa verde em silêncio.


class LancCCCabecalho(BaseModel):
    """`cabecalho` do `IncluirLancCC` — conta, data e valor do lançamento."""

    n_cod_cc: int = Field(
        alias="nCodCC",
        description="ID da conta corrente do cartão no Omie (`nCodCC` da sessão).",
    )
    d_dt_lanc: str = Field(
        alias="dDtLanc",
        description="Data do lançamento no formato Omie `dd/mm/aaaa`.",
    )
    n_valor_lanc: Decimal = Field(
        alias="nValorLanc",
        description=(
            "Valor **absoluto** (sempre positivo), 2 casas. ⚠️ Sem `cNatureza` "
            "na escrita, a direção débito/crédito é INDETERMINADA até o "
            "readback da captura — ver `IncluirLancCCRequest`."
        ),
    )

    @field_serializer("n_valor_lanc", when_used="json")
    def _valor_como_numero(self, valor: Decimal) -> float:
        """No fio, `nValorLanc` é NÚMERO JSON, não string.

        A doc declara `decimal` e o exemplo oficial mostra `123.46` sem aspas;
        o Pydantic v2 serializa `Decimal` como string em `mode="json"`, e a
        string é a suspeita nº 1 do `3102` genérico da captura de 21/08/2026.
        O float existe SÓ nesta borda de serialização (§3.4 continua valendo:
        o valor interno é `Decimal`); com 2 casas decimais, o repr mais curto
        do float reproduz o decimal exato.
        """
        return float(valor)

    model_config = ConfigDict(populate_by_name=True)


class LancCCDetalhes(BaseModel):
    """`detalhes` do `IncluirLancCC` — classificação e texto livre.

    A doc declara também `cNumDoc`, `nCodCliente` e `nCodProjeto`; não são
    declarados aqui porque o ADL não os usa — declarar campo que não se envia
    só aumentaria a superfície de divergência (mesmo racional do
    `CategoriaOmie`).
    """

    c_cod_categ: str = Field(
        alias="cCodCateg",
        description="Código da categoria Omie (`string20`) — não vem da fatura.",
    )
    c_obs: str | None = Field(
        default=None,
        alias="cObs",
        description="Observação livre — descrição da compra. Nunca logada (PII, §4.5).",
    )
    c_tipo: str | None = Field(
        default=None,
        alias="cTipo",
        description=(
            "Tipo do documento (`string5`). A doc o trata como essencial e o "
            "exemplo oficial envia `DIN`; a ausência dele é a suspeita nº 2 "
            "do `3102` da captura de 21/08/2026. Enviamos `DIN` até a fixture "
            "de aceite arbitrar."
        ),
    )

    model_config = ConfigDict(populate_by_name=True)


class IncluirLancCCRequest(BaseModel):
    """Parâmetro do `IncluirLancCC` (`financas/contacorrentelancamentos/`).

    Forma ANINHADA conforme a doc oficial, **corroborada pela recusa real do
    formato plano em 21/08/2026** (ver o cabeçalho da seção): `cCodIntLanc` no
    topo; `cabecalho` { `nCodCC`, `dDtLanc`, `nValorLanc` }; `detalhes`
    { `cCodCateg`, `cTipo`, `cNumDoc`, `nCodCliente`, `nCodProjeto`, `cObs` };
    `transferencia` e `departamentos` opcionais (não usados).

    ⚠️ **O que SEGUE não-verificado (S-1)** até uma captura ACEITA:

    - **Representação do sinal**: `cNatureza` NÃO existe no contrato de
      escrita (na doc ele só aparece na estrutura `diversos`, domínio `P`/`R`
      — outra coisa). Candidatos: `nValorLanc` com sinal, ou a natureza da
      própria categoria. **Enquanto indeterminado, o serviço só monta COMPRA
      (débito) com valor absoluto e BLOQUEIA estorno** — ver
      `_eligibility_block` no `omie_posting/service.py`. O readback da captura
      (extrato relido após o aceite) é o que responde a direção.
    - ~~`cCodIntLanc` como chave idempotente~~ → **VERIFICADO em 21/08/2026**:
      o 2º POST do mesmo código devolveu o MESMO `nCodLanc` (status 0), sem
      criar segundo lançamento. Segue não sendo a defesa primária do ADL —
      ver BACK 07.2.
    - **Nomes internos e obrigatoriedade** de `cabecalho`/`detalhes`: o ACEITE
      de 21/08/2026 (com `nValorLanc` numérico e `cTipo='DIN'`) prova o
      conjunto enviado; o gate compara a fixture contra
      `omie_param_aliases()`.
    """

    c_cod_int_lanc: str | None = Field(
        default=None,
        alias="cCodIntLanc",
        description=(
            "Chave de integração por LINHA da fatura (`string20`), no TOPO do "
            "param (fora do `cabecalho`). ✅ VERIFICADO (captura 21/08/2026): "
            "o Omie é IDEMPOTENTE sobre ela — o 2º POST do mesmo código "
            "devolveu o MESMO `nCodLanc` com status 0, sem criar segundo "
            "lançamento. A dedup primária continua sendo do ADL (BACK 07.2)."
        ),
    )
    cabecalho: LancCCCabecalho
    detalhes: LancCCDetalhes

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def omie_param_aliases(cls) -> frozenset[str]:
        """Chaves que este DTO pode enviar à Omie, como caminhos pontilhados.

        Ex.: `{"cCodIntLanc", "cabecalho", "cabecalho.nCodCC", ...}`. Usado
        pelo gate de fixture: se alguém renomear/adicionar campo sem recapturar
        uma chamada real, o teste acusa a divergência. O gate achata o `param`
        capturado no mesmo formato antes de comparar.
        """

        def walk(model: type[BaseModel], prefix: str) -> Iterator[str]:
            for name, field in model.model_fields.items():
                alias = field.alias or name
                path = f"{prefix}{alias}"
                yield path
                candidates = (field.annotation, *get_args(field.annotation))
                sub = next(
                    (c for c in candidates if isinstance(c, type) and issubclass(c, BaseModel)),
                    None,
                )
                if sub is not None:
                    yield from walk(sub, f"{path}.")

        return frozenset(walk(cls, ""))


class IncluirLancCCResponse(BaseModel):
    """Resposta do `IncluirLancCC`.

    ⚠️ **NÃO-VERIFICADO (S-1).** Os nomes abaixo vêm da convenção da Omie
    para os serviços `Incluir*` (eco do código interno + código/descrição de
    status), **não** de uma resposta real. Cross-check da doc (19/08/2026):
    os 4 campos batem 1:1 com a `lanccIncluirResponse` documentada — ao
    contrário do request, aqui doc e DTO concordam; segue valendo a fixture
    como prova final. `n_cod_lanc` é declarado
    **obrigatório** de propósito: é o dado que o ADL precisa persistir
    (`omie_lancamento_id`, BACK 07.2) e, se o nome real for outro, o teste
    contra a fixture FALHA em vez de gravar `None` em silêncio — que foi
    exatamente o modo de falha do `ListarExtrato` v1 (ver `LancamentoExtrato`).
    """

    n_cod_lanc: int = Field(
        alias="nCodLanc",
        description="ID do lançamento criado no Omie. ⚠️ Nome NÃO-VERIFICADO.",
    )
    c_cod_int_lanc: str | None = Field(
        default=None,
        alias="cCodIntLanc",
        description="Eco da chave de integração enviada. ⚠️ NÃO-VERIFICADO.",
    )
    c_cod_status: str | None = Field(
        default=None,
        alias="cCodStatus",
        description="Código de status da inclusão. ⚠️ NÃO-VERIFICADO.",
    )
    c_des_status: str | None = Field(
        default=None,
        alias="cDesStatus",
        description="Descrição do status da inclusão. ⚠️ NÃO-VERIFICADO.",
    )

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


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
