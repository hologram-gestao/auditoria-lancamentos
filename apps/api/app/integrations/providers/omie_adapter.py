"""Adaptador Omie — o PRIMEIRO `OriginProvider` (Sprint 9, BACK 09.2 — R3).

Embrulha o `OmieClient`, que continua sendo a única coisa que fala com a API da
Omie e continua **intocado**: todo contrato verificado contra fixture real
(`tests/fixtures/omie/`) segue valendo, e o `omie_posting` continua usando o
client cru, sem passar por aqui. Este arquivo só traduz — nomes do Omie para os
DTOs neutros, erros do Omie para os erros neutros.

**A resolução do cliente-demo mudou de casa.** O prefixo `FAKE_DEMO_OMIE_` que
troca `OmieClient` por `MockOmieClient` morava em `modules/clients/omie_factory.py`;
agora mora em `build_omie_raw_client`, aqui. Motivo: o seed demo
(`scripts/seed_demo_client.py`), o ambiente local de validação e o e2e mockado
dependem desse prefixo, e a partir da 09.3 a credencial pode chegar por
`client_connections` em vez das colunas de `clients` — se a heurística ficasse
no factory antigo, o caminho novo entraria em produção sem ela e o demo iria
para a rede de verdade. Uma casa só, servindo os dois caminhos.

**As 4 capacidades.** O Omie verifica credencial, lista contas, lista
lançamentos **e escreve** (`IncluirLancCC`, Sprint 7 — §3.16). Declarar
`ESCREVER` aqui é dizer o que o provedor SABE fazer; se aquele lançamento pode
sair é outra decisão, a do kill-switch `OMIE_POSTING_ENABLED`, e ela continua
onde está.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pydantic import SecretStr

from app.core.exceptions import (
    OmieAuthError,
    OmieFaultError,
    ProviderAuthError,
    ValidationAppError,
)
from app.core.logging import get_logger
from app.db.models.client_connection import ProviderType
from app.integrations.omie.client import OmieClient, OmieCredentials
from app.integrations.omie.mock_client import FAKE_DEMO_KEY_PREFIX, MockOmieClient
from app.integrations.omie.schemas import OmieTituloStatus
from app.integrations.providers.base import (
    Capability,
    ProviderAccount,
    ProviderCredentials,
    ProviderEntry,
    ProviderOpenTitle,
    ProviderTitleKind,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    import httpx

    from app.core.config import Settings
    from app.integrations.omie.schemas import TituloAPagarReceber

log = get_logger(__name__)

#: Chaves do JSON de credencial do Omie. Fonte ÚNICA — a 09.3 valida o payload
#: de criação de conexão contra elas, e o adaptador as lê daqui.
OMIE_CREDENTIAL_KEYS = ("app_key", "app_secret")

#: O que o Omie sabe fazer. Congelado como `frozenset` para não ser mutado por
#: engano por quem só queria ler.
OMIE_CAPABILITIES = frozenset(
    {
        Capability.VERIFICAR_CREDENCIAL,
        Capability.LISTAR_CONTAS,
        Capability.LISTAR_LANCAMENTOS,
        Capability.ESCREVER,
        Capability.LISTAR_TITULOS_EM_ABERTO,
    }
)

#: Os status que, para a Omie, significam "título ainda não liquidado".
#: `ATRASADO` (vencido) + `AVENCER` (vencimento futuro) — os dois únicos valores
#: que o `filtrar_por_status` aceita e que a carteira quer. ⚠️ **Não** acrescentar
#: `"PREVISTO"`: a Omie devolve 5001 (caso real em prod, 19/05/2026), porque esse
#: valor não existe no enum oficial. Ver `OmieTituloStatus`.
_OPEN_TITLE_STATUSES = (OmieTituloStatus.ATRASADO, OmieTituloStatus.AVENCER)

#: Pausa entre chamadas consecutivas à Omie, **copiada** de
#: `reconciliations/processing/omie_fetch.py`. Não é folclore: sem o intervalo, a
#: segunda chamada do mesmo método com a mesma credencial volta `1880`. O plano de
#: chamadas abaixo também alterna PAGAR/RECEBER pelo mesmo motivo — duas chamadas
#: do MESMO endpoint nunca ficam adjacentes.
_INTER_CALL_DELAY_SECONDS = 1.5

#: `faultstring` da Omie que significa "essa tag não pertence ao tipo complexo" —
#: é assim que ela recusaria a listagem sem o filtro de conta corrente. É o
#: gatilho do ramo (b) do R1.
_FAULT_CODE_TAG_INVALIDA = "5001"


def build_omie_raw_client(
    credentials: OmieCredentials,
    settings: Settings,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> OmieClient:
    """`OmieClient` (ou `MockOmieClient`) pronto — o ÚNICO lugar que decide qual.

    Heurística de cliente-demo: credencial gerada por `seed_demo_client.py`
    começa com `FAKE_DEMO_OMIE_`. O prefixo é improvável numa key real e o Omie
    de produção nunca o aceitaria — usá-lo como flag implícita evita coluna e
    migration, e mantém o seed como única fonte de ativação. NUNCA usar prefixo
    parecido em credencial real.
    """
    if credentials.app_key.get_secret_value().startswith(FAKE_DEMO_KEY_PREFIX):
        return MockOmieClient(credentials, settings)
    return OmieClient(credentials, settings, http_client=http_client)


def omie_credentials_from(credentials: ProviderCredentials) -> OmieCredentials:
    """Mapa neutro → par tipado do Omie. Chave faltando é 422, não `KeyError`."""
    missing = [key for key in OMIE_CREDENTIAL_KEYS if key not in credentials]
    if missing:
        raise ValidationAppError(
            f"omie credentials missing keys: {', '.join(missing)}",
            user_message="A conexão com o Omie exige App Key e App Secret.",
        )
    return OmieCredentials(
        app_key=credentials["app_key"],
        app_secret=credentials["app_secret"],
    )


class OmieProvider:
    """`OriginProvider` do Omie. Traduz, não reimplementa."""

    def __init__(
        self,
        credentials: ProviderCredentials,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = build_omie_raw_client(
            omie_credentials_from(credentials), settings, http_client=http_client
        )

    @property
    def provider_type(self) -> str:
        return ProviderType.OMIE.value

    @property
    def capabilities(self) -> frozenset[Capability]:
        return OMIE_CAPABILITIES

    @property
    def raw_client(self) -> OmieClient:
        """Escotilha para o que já está verificado contra fixture real.

        `omie_posting` e o resto do fluxo do Omie continuam falando com o client
        cru: reescrevê-los atrás dos DTOs neutros nesta task seria mexer em
        contrato validado por captura, sem necessidade nenhuma.
        """
        return self._client

    async def verify_credentials(self) -> None:
        """`ListarClientes` com página de 1 — a chamada mais barata que autentica."""
        try:
            await self._client.listar_clientes_minimal()
        except OmieAuthError as exc:
            raise ProviderAuthError(str(exc)) from exc

    async def list_accounts(self) -> list[ProviderAccount]:
        try:
            contas = await self._client.listar_contas_correntes()
        except OmieAuthError as exc:
            raise ProviderAuthError(str(exc)) from exc
        return [
            ProviderAccount(
                external_id=str(conta.n_cod_cc),
                name=conta.descricao,
                bank_code=conta.codigo_banco,
                account_type=conta.tipo,
            )
            for conta in contas
        ]

    async def list_entries(
        self, *, account_external_id: str, start: date, end: date
    ) -> list[ProviderEntry]:
        """Extrato do período. `account_external_id` é o `nCodCC`, como texto."""
        try:
            n_cod_cc = int(account_external_id)
        except ValueError as exc:
            raise ValidationAppError(
                f"omie account id is not numeric: {account_external_id!r}",
                user_message="A conta informada não é válida para o Omie.",
            ) from exc
        try:
            lancamentos = await self._client.listar_extrato(
                n_cod_cc=n_cod_cc, data_inicial=start, data_final=end
            )
        except OmieAuthError as exc:
            raise ProviderAuthError(str(exc)) from exc
        return [
            ProviderEntry(
                external_id=str(lanc.n_cod_lancamento),
                entry_date=lanc.d_data_lancamento,
                # Já com sinal: a convenção de natureza (D/C de conta corrente,
                # P/R de cartão) morre aqui, no adaptador.
                amount=lanc.signed_amount,
                description=lanc.description,
                status=lanc.c_situacao,
                # CÓDIGO, nunca nome (§4.5) — descrição de categoria e razão
                # social são resolvidas em runtime, com cache TTL.
                category_code=lanc.c_cod_categoria,
                supplier_code=str(lanc.n_cod_cliente) if lanc.n_cod_cliente else None,
            )
            for lanc in lancamentos
        ]

    async def list_open_titles(
        self, *, known_account_external_ids: Sequence[str] = ()
    ) -> list[ProviderOpenTitle]:
        """A CARTEIRA do cliente: títulos não liquidados, todas as contas (R1).

        **Reusa `listar_contas_pagar`/`listar_contas_receber`** — nenhum cliente
        HTTP novo, nenhuma segunda implementação de paginação. O que muda é
        exclusivamente o RECORTE: sem `filtrar_conta_corrente` e sem
        `filtrar_por_data_*`. É por isso que um título vencido há quatro meses
        volta, e a leitura da conciliação (uma conta, um mês) segue intocada.

        **Os DOIS ramos do R1, os dois implementados:**

        (a) tenta sem o filtro de conta. Este é o caminho esperado, e não é
            palpite: a captura real em `tests/fixtures/omie/` foi feita
            exatamente assim (`param` com só `pagina`/`registros_por_pagina`) e
            devolveu 3.896 registros em 78 páginas;

        (b) se a origem **recusar** a chamada sem o filtro — e a Omie recusa
            respondendo **HTTP 200 com `faultstring`**, tipicamente `5001` "Tag
            não faz parte da estrutura do tipo complexo" —, itera as contas
            conhecidas do cliente **serialmente** e une os resultados. Mesmo
            resultado, custo em requisições multiplicado pelo número de contas.

        **Serialização é obrigatória, não otimização às avessas.** A Omie
        processa uma requisição por método por credencial; `asyncio.gather` aqui
        renderia `8020`/`1880`. O plano de chamadas alterna PAGAR/RECEBER e dorme
        `_INTER_CALL_DELAY_SECONDS` entre elas, copiando o que a conciliação já
        faz — não redescobrindo.

        **Dedup por identificador.** Um título "Atrasado" pode aparecer nas duas
        passadas de status, e no ramo (b) o mesmo título pode voltar por duas
        contas se a origem ignorar o filtro. A última ocorrência vence: o upsert
        do repositório recusaria o mesmo par `(cliente, identificador)` duas
        vezes no MESMO comando (`cannot affect row a second time`), e a
        sincronização inteira morreria por um problema que não é do cliente.
        """
        try:
            titles = await self._fetch_open_titles(conta_corrente_id=None)
        except OmieFaultError as exc:
            if not self._is_tag_rejection(exc):
                raise
            log.info(
                "omie_open_titles_account_filter_required",
                accounts=len(known_account_external_ids),
            )
            titles = await self._fetch_open_titles_per_account(known_account_external_ids)

        # A última ocorrência vence — ver o docstring.
        by_id = {title.external_id: title for title in titles}
        return list(by_id.values())

    async def _fetch_open_titles_per_account(
        self, known_account_external_ids: Sequence[str]
    ) -> list[ProviderOpenTitle]:
        """Ramo (b): uma passada por conta conhecida, **em série**.

        Conta cujo identificador não é numérico é ignorada com log em vez de
        derrubar a ingestão: o `nCodCC` do Omie é inteiro, e uma conta com
        identificador de outro formato no cache é dado velho, não motivo para o
        cliente ficar sem carteira.
        """
        titles: list[ProviderOpenTitle] = []
        for account in known_account_external_ids:
            try:
                conta_corrente_id = int(account)
            except ValueError:
                log.warning("omie_open_titles_skipped_non_numeric_account")
                continue
            titles.extend(await self._fetch_open_titles(conta_corrente_id=conta_corrente_id))
        return titles

    async def _fetch_open_titles(self, *, conta_corrente_id: int | None) -> list[ProviderOpenTitle]:
        """As quatro chamadas (2 cadastros x 2 status), serializadas e alternadas."""
        call_plan: list[tuple[ProviderTitleKind, OmieTituloStatus]] = [
            (kind, status)
            for status in _OPEN_TITLE_STATUSES
            for kind in (ProviderTitleKind.A_PAGAR, ProviderTitleKind.A_RECEBER)
        ]

        titles: list[ProviderOpenTitle] = []
        for index, (kind, status) in enumerate(call_plan):
            if index > 0:
                await asyncio.sleep(_INTER_CALL_DELAY_SECONDS)
            listar = (
                self._client.listar_contas_pagar
                if kind is ProviderTitleKind.A_PAGAR
                else self._client.listar_contas_receber
            )
            try:
                found = await listar(conta_corrente_id=conta_corrente_id, status=status)
            except OmieAuthError as exc:
                raise ProviderAuthError(str(exc)) from exc
            titles.extend(_open_title_from(titulo, kind=kind) for titulo in found)
        return titles

    @staticmethod
    def _is_tag_rejection(exc: OmieFaultError) -> bool:
        """A recusa é "tag inválida" (ramo b) ou outra falha (que propaga)?

        Só o `5001` desvia para o ramo (b). Tratar qualquer `faultstring` como
        "preciso do filtro de conta" transformaria credencial expirada e
        instabilidade da origem em 78 paginas x N contas de requisições inúteis
        antes de falhar de novo.
        """
        return exc.metadata.get("fault_code") == _FAULT_CODE_TAG_INVALIDA

    async def aclose(self) -> None:
        await self._client.aclose()


def _open_title_from(titulo: TituloAPagarReceber, *, kind: ProviderTitleKind) -> ProviderOpenTitle:
    """`TituloAPagarReceber` → DTO neutro. **Só códigos** (§4.5).

    Fica de fora, de propósito: `observacao` (texto livre em que a Omie ecoa
    descrição de compra e nome de fornecedor) e qualquer campo de nome. O
    `valor_documento` vai em valor ABSOLUTO — o sinal seria redundante com
    `kind`, e dois campos dizendo a mesma coisa é um deles podendo discordar.
    """
    return ProviderOpenTitle(
        external_id=str(titulo.codigo_lancamento_omie),
        kind=kind,
        due_date=titulo.data_vencimento,
        amount=abs(titulo.valor_documento),
        situation=titulo.status_titulo,
        category_code=titulo.codigo_categoria,
        supplier_code=(
            str(titulo.codigo_cliente_fornecedor)
            if titulo.codigo_cliente_fornecedor is not None
            else None
        ),
        account_external_id=(
            str(titulo.id_conta_corrente) if titulo.id_conta_corrente is not None else None
        ),
        document_number=titulo.numero_documento,
    )


def omie_credentials_payload(app_key: str, app_secret: str) -> ProviderCredentials:
    """Atalho tipado para montar o mapa do Omie a partir de texto decifrado."""
    return {"app_key": SecretStr(app_key), "app_secret": SecretStr(app_secret)}
