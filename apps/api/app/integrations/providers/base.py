"""Contrato ÚNICO de provedor de origem (Sprint 9, BACK 09.2 — R3).

Até aqui "origem de dado" e "Omie" eram a mesma palavra: o cliente guardava um
par de credenciais Omie e todo consumidor falava com `OmieClient` direto. Este
módulo separa as duas coisas — **o que o ADL precisa de uma origem** (verificar
credencial, listar contas, listar lançamentos, escrever) fica aqui, e **como o
Omie faz cada uma** fica no adaptador.

**O objetivo é o segundo provedor CABER, não existir.** Não há plugin loader,
não há tabela de provedores e não há um 2º adaptador nesta sprint. O que existe
é: um enum fechado de capacidades, um Protocol com quatro operações, DTOs
neutros e um registry que resolve tipo → adaptador.

**Capacidade é DADO, não exceção.** Cada adaptador declara o que sabe fazer
(`capabilities`), e isso viaja para a API na resposta de conexão. O consumidor
pergunta antes de tentar, em vez de descobrir por um 500 vindo do provedor.

**Credencial nunca trafega como `str`.** O tipo de trânsito é
`ProviderCredentials` — um mapa de `SecretStr` cujo `repr()` já sai mascarado, e
que o redactor do structlog cobre por chave. Quem precisa do texto chama
`.get_secret_value()` no ÚLTIMO momento, dentro do adaptador.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, SecretStr

#: Credencial em trânsito: `{"app_key": SecretStr(...), "app_secret": SecretStr(...)}`
#: para o Omie, `{"token": SecretStr(...)}` para um provedor de token, e assim por
#: diante. É este mapa que a 09.3 cifra inteiro, como JSON, em
#: `client_connections.credentials_encrypted` (AAD_CONNECTION_CREDENTIALS).
type ProviderCredentials = dict[str, SecretStr]


class Capability(StrEnum):
    """O que uma origem sabe fazer — enum FECHADO.

    Fechado de propósito: capacidade é contrato entre o adaptador e as rotas, e
    o front desenha em cima dela. Capacidade nova entra aqui, no adaptador que a
    implementa e no schema de resposta — nunca como string solta num `if`.
    """

    VERIFICAR_CREDENCIAL = "verificar_credencial"
    LISTAR_CONTAS = "listar_contas"
    LISTAR_LANCAMENTOS = "listar_lancamentos"
    ESCREVER = "escrever"
    #: Sprint 11 — a CARTEIRA: títulos a pagar e a receber **não liquidados**, de
    #: **todas** as contas correntes, **sem recorte de competência**. É uma
    #: capacidade separada de `LISTAR_LANCAMENTOS` de propósito: lançamento é
    #: movimento realizado numa conta e num período (o extrato), título em aberto
    #: é compromisso futuro sem conta nem período — um provedor pode
    #: perfeitamente ter o primeiro e não ter o segundo, e a tela da carteira
    #: precisa saber disso ANTES de tentar.
    LISTAR_TITULOS_EM_ABERTO = "listar_titulos_em_aberto"


class ProviderTitleKind(StrEnum):
    """A pagar ou a receber, em forma NEUTRA.

    Os valores são **os mesmos** de `db.models.client_title.TitleType`, e
    `tests/unit/test_provider_open_title_contract.py` compara as duas listas: o
    DTO não importa modelo de banco (a camada de integração não conhece o
    schema), então a coincidência precisa ser vigiada em vez de suposta.
    """

    A_PAGAR = "a_pagar"
    A_RECEBER = "a_receber"


class ProviderAccount(BaseModel):
    """Uma conta da origem, em forma NEUTRA.

    `external_id` é `str` mesmo quando o provedor usa inteiro (o `nCodCC` do
    Omie): identificador de terceiro não é número nosso, e supor `int` fecharia
    a porta para provedor que usa UUID ou código alfanumérico. Quem persiste em
    coluna numérica converte na borda.
    """

    model_config = ConfigDict(frozen=True)

    external_id: str
    name: str
    bank_code: str | None = None
    #: Código de tipo de conta DO PROVEDOR, verbatim (no Omie: `CC`, `CR`, `CA`…).
    #: Não é traduzido aqui: a tradução vive onde a regra de negócio está
    #: (`SessionAccountType`), e inventar um enum neutro agora seria adivinhar o
    #: vocabulário do 2º provedor antes de ele existir.
    account_type: str


class ProviderEntry(BaseModel):
    """Um lançamento da origem, em forma NEUTRA.

    Só o que o ADL usa para conciliar. **Valor já COM SINAL** (a convenção de
    natureza é do provedor e morre no adaptador — ver `LancamentoExtrato.signed_amount`,
    que cobre `D/C` de conta corrente e `P/R` de cartão). **Nome não vem aqui:**
    razão social e descrição de categoria são resolvidos em runtime e vivem em
    cache TTL (§4.5) — o que trafega é CÓDIGO.
    """

    model_config = ConfigDict(frozen=True)

    external_id: str
    entry_date: date
    amount: Decimal
    description: str = ""
    status: str | None = None
    category_code: str | None = None
    supplier_code: str | None = None


class ProviderOpenTitle(BaseModel):
    """Um título EM ABERTO da origem, em forma NEUTRA (Sprint 11, BACK 11.2).

    **Só códigos, nunca nome (§4.5).** `supplier_code` é o código do
    devedor/credor no cadastro da origem; a razão social continua resolvida em
    runtime, pelo `clientes_cache` que a aba de divergências já usa. Não existe
    aqui campo de nome nem `observacao`: observação é texto livre de terceiro, e
    a Omie ecoa nela a descrição da compra (§4.5/§3.16).

    **`amount` é o valor do documento, SEM sinal.** Diferente de
    `ProviderEntry.amount` (que já vem com sinal porque o extrato tem natureza),
    aqui o sinal é redundante: `kind` já diz se é obrigação ou direito, e
    inventar sinal faria a soma do aging depender de qual dos dois campos o
    consumidor olhou. `Decimal` sempre — nunca `float` (§3.4).

    **`situation` é o rótulo de situação VERBATIM da origem** (no Omie:
    `status_titulo`, ex.: `'ATRASADO'`). Ele **não é persistido**: serve para o
    serviço derivar o `status` da carteira, e guardar o rótulo do terceiro no
    banco criaria um segundo vocabulário de estado ao lado do nosso.

    **`account_external_id` é nulável** porque é dado da origem: o Omie devolve
    `id_conta_corrente` em todo título (verificado na captura real), mas um
    provedor sem o conceito de conta corrente não teria o que preencher.
    """

    model_config = ConfigDict(frozen=True)

    external_id: str
    kind: ProviderTitleKind
    due_date: date
    amount: Decimal
    situation: str = ""
    category_code: str | None = None
    supplier_code: str | None = None
    account_external_id: str | None = None
    document_number: str | None = None


@runtime_checkable
class OriginProvider(Protocol):
    """O que qualquer origem precisa saber fazer.

    `Protocol` e não ABC: o adaptador não herda nada, só cumpre a forma — o que
    mantém o `OmieClient` cru (verificado contra fixture real) intocado por
    baixo. `runtime_checkable` para o teste do registry conseguir afirmar a
    conformidade sem instanciar o mundo.

    Implementação que não cobre uma operação **não a declara em `capabilities`**
    e o predicado da conexão (`modules/client_connections/capability.py`) a
    impede de ser escolhida — em vez de levantar `NotImplementedError` no meio
    de um job.
    """

    @property
    def provider_type(self) -> str: ...

    @property
    def capabilities(self) -> frozenset[Capability]: ...

    async def verify_credentials(self) -> None:
        """Bate no provedor com a chamada mais barata possível.

        Volta `None` em sucesso. Credencial recusada levanta `ProviderAuthError`
        — e é ESSE erro (não timeout, não instabilidade) que marca a conexão
        como `erro`.
        """
        ...

    async def list_accounts(self) -> list[ProviderAccount]: ...

    async def list_entries(
        self, *, account_external_id: str, start: date, end: date
    ) -> list[ProviderEntry]: ...

    async def list_open_titles(
        self, *, known_account_external_ids: Sequence[str] = ()
    ) -> list[ProviderOpenTitle]:
        """A CARTEIRA: todos os títulos não liquidados, todas as contas (S11 R1).

        **Sem recorte de competência e sem recorte de conta** — é o oposto exato
        da leitura da conciliação, e é o que faz um título vencido há quatro
        meses voltar.

        `known_account_external_ids` é o **ramo (b)** do R1, e existe como
        parâmetro em vez de como decisão interna do adaptador porque quem sabe
        quais contas o cliente tem é a camada de cima (o cache de contas do
        cliente), não a integração. O adaptador tenta primeiro **sem** filtro de
        conta; se a origem recusar a chamada sem ele, itera estas contas
        **serialmente** e une os resultados. O resultado é o mesmo nos dois
        ramos — muda só o custo em requisições.

        Implementação que não sabe fazer isso **não declara**
        `Capability.LISTAR_TITULOS_EM_ABERTO`, e a rota responde
        `capacidade_ausente` em vez de estourar aqui.
        """
        ...

    async def aclose(self) -> None: ...
