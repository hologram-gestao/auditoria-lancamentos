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
    c_cod_int_lanc: str | None = Field(
        default=None,
        alias="cCodIntLanc",
        description=(
            "Chave de integração do lançamento. ⚠️ **NÃO-VERIFICADO** que o "
            "`ListarExtrato` devolva este campo (S-1) — por isso é opcional e "
            "com default `None`. É o que permite a reconciliação pós-timeout "
            "da BACK 07.4 ser CONCLUSIVA: se o campo vier populado em alguma "
            "linha, dá para afirmar se o lançamento entrou; se não vier em "
            "nenhuma, o resultado é INCONCLUSIVO e o ADL **não reenvia**. "
            "Ausente ⇒ nunca leva a um lançamento duplicado."
        ),
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
# ⚠️ **TODO O CONTRATO ABAIXO É NÃO-VERIFICADO CONTRA A API REAL (S-1).**
# Origem dos nomes: doc oficial da Omie citada no PRD da Sprint 7
# (https://developer.omie.com.br/service-list/, serviço
# `financas/contacorrentelancamentos/`) — NÃO uma resposta real. É exatamente
# a situação do defeito P11 (Sprint 1): um contrato plausível, apresentado
# como fato, implementado contra um mock que repetia a invenção.
#
# O gate que fecha essa lacuna é `tests/unit/test_omie_fixtures.py`, que roda
# estes DTOs contra a fixture gravada por `scripts/capture_omie_fixtures.py`
# (opt-in de escrita). Enquanto a fixture não existir, o teste SKIPA citando
# S-1 — nunca passa verde em silêncio.


class IncluirLancCCRequest(BaseModel):
    """Parâmetro do `IncluirLancCC` (`financas/contacorrentelancamentos/`).

    ⚠️ **NÃO-VERIFICADO (S-1)** — os pontos abaixo são suposição documentada,
    não fato observado. Cada um tem uma origem declarada:

    - **Convenção de sinal** (`nValorLanc` absoluto + `cNatureza` carregando o
      sinal): verificada apenas no lado de **LEITURA** do mesmo domínio
      (`ListarExtrato` — ver o cabeçalho deste módulo e `LancamentoExtrato`).
      **Não** está confirmado que a escrita siga a mesma convenção. Assumir
      "valor negativo" seria fabricar o oposto do que o repositório já sabe;
      por isso seguimos a convenção de leitura E marcamos como não-verificada.
    - **`cCodIntLanc` como chave idempotente imposta pelo Omie**: NÃO
      confirmado. Não é a defesa primária do ADL — ver BACK 07.2, onde a dedup
      mora no banco do próprio ADL. Aqui é só defesa adicional.
    - **`cTipo`**: valor `"DIN"` citado no PRD é **palpite**. O campo análogo
      do lado de leitura é `string3` com `PAG`/`ATR`
      (`TituloAPagarReceber.status_titulo`). Por isso o campo é opcional e
      **só deve ser enviado se a fixture real confirmar um valor válido**.
    - **Nomes de campo e obrigatoriedade**: vêm da doc, não de uma resposta.
      Quando a fixture existir, o teste compara as chaves realmente aceitas
      pela Omie com o conjunto de aliases deste DTO e FALHA na divergência.

    **Cross-check contra a doc oficial (19/08/2026 — duas leituras
    independentes de `financas/contacorrentelancamentos/`): a doc descreve o
    `param` ANINHADO, este DTO emite PLANO.** Estrutura na doc: `cCodIntLanc`
    no topo; `cabecalho` { `nCodCC`, `dDtLanc`, `nValorLanc` }; `detalhes`
    { `cCodCateg`, `cTipo`, `cNumDoc`, `nCodCliente`, `nCodProjeto`, `cObs` };
    `transferencia` e `departamentos` opcionais. Além da forma:

    - **`cNatureza` NÃO existe no contrato de escrita** — na página ele só
      aparece na estrutura `diversos`, com domínio `P`/`R` (outra coisa). Como
      a doc não mostra campo de sinal no `cabecalho`, a representação de
      débito/crédito na escrita é INDETERMINADA (candidatos: `nValorLanc` com
      sinal, ou a natureza da própria categoria). O readback da captura é o
      que responde isso.
    - **`cTipo` não era palpite**: `string5` em `detalhes`, com `DIN` entre os
      valores válidos (`ADI, BOL, CRT, CHQ, CON, CRE, DRF, DAS, DEB, DIN,
      DOC, GUIA, PROT, REC, RPA, TED, TRA, 99999`). A página não marca a
      obrigatoriedade de forma inequívoca (nem a de `cCodCateg`).

    O DTO **não** foi reescrito para o formato aninhado de propósito: seria
    trocar uma suposição por outra da MESMA fonte que já errou 3x neste
    repositório (`ListarExtrato` v1, `ListarContasCorrentes`, filtro
    `PREVISTO` → 5001). Se a doc estiver certa, o 1º POST real falha SEM criar
    lançamento — a faultstring é evidência de graça, e o script de captura a
    grava verbatim. Com a fixture na mão, a reescrita é mecânica.
    """

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
            "Valor **absoluto** (sempre positivo), 2 casas. ⚠️ NÃO-VERIFICADO "
            "no lado de escrita — o sinal viaja em `cNatureza` (convenção "
            "confirmada só na leitura)."
        ),
    )
    c_natureza: str = Field(
        alias="cNatureza",
        description="'D' (débito/compra) ou 'C' (crédito/estorno). ⚠️ NÃO-VERIFICADO na escrita.",
    )
    c_cod_categ: str = Field(
        alias="cCodCateg",
        description="Código da categoria Omie (`string20`) — obrigatório, não vem da fatura.",
    )
    c_cod_int_lanc: str | None = Field(
        default=None,
        alias="cCodIntLanc",
        description=(
            "Chave de integração por LINHA da fatura (`string20`). ⚠️ Que o "
            "Omie **imponha** unicidade sobre ela é NÃO-VERIFICADO (S-1) — a "
            "dedup primária é do ADL (BACK 07.2)."
        ),
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
            "Tipo do documento. ⚠️ NÃO-VERIFICADO: o valor `'DIN'` citado no "
            "PRD é palpite. Enviar apenas se a fixture real confirmar."
        ),
    )

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def omie_param_aliases(cls) -> frozenset[str]:
        """Conjunto de chaves que este DTO envia à Omie (aliases camelCase).

        Usado pelo gate de fixture: se alguém renomear/adicionar campo sem
        recapturar uma chamada real, o teste da fixture acusa a divergência.
        """
        return frozenset(field.alias or name for name, field in cls.model_fields.items())


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
