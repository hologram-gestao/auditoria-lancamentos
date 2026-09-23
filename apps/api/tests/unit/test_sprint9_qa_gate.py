"""Guardas do QA para a Sprint 9 — cliente sem origem e conexões plugáveis.

Este arquivo é do **QA**, não dos executores, e existe para travar as
invariantes da sprint que **não dependem de banco nem de rede**. É deliberado:
a bateria de integração da 09.3 (`tests/integration/test_client_connections.py`)
cobre o comportamento ponta a ponta, mas só roda com Postgres de pé — e numa
máquina sem Docker ela **pula em massa**, o que deixaria a afirmação central da
sprint ("nenhuma resposta carrega credencial") sem NENHUM guardião executado.
Aqui o custo de rodar é zero, então a guarda vale sempre.

O que cada bloco trava:

    1. **Nenhum schema de resposta do módulo de conexões declara credencial** —
       nem em claro, nem cifrada, nem mascarada. A varredura é sobre os CAMPOS
       DECLARADOS, não sobre um payload de exemplo: um `app_key: "****"` que
       alguém acrescentasse "só para a tela mostrar" morre aqui, antes de existir
       um valor para mascarar (§3.2).
    2. **A derivação de `origin_status` é ÚNICA e total** — os três estados, com
       a tabela-verdade inteira. Um quarto estado ou uma segunda cópia da regra
       é o começo da "terceira verdade" que a 09.4 escreveu a função para evitar.
    3. **A taxonomia 409 é fechada em três e cada situação devolve o SEU código**
       — é o que permite à tela dizer "conectar", "reconectar" ou "não há o que
       consertar" em vez de um toast genérico.
    4. **A conexão sintetizada da janela de conversão nunca carrega segredo** e
       é reconhecível — se ela vazasse credencial na própria linha, o fallback
       da 09.5 teria criado uma segunda cópia do ciphertext fora do script de
       conversão, que é exatamente o que o R2 proíbe.
    5. **A permissão nova está na matriz dos DOIS lados com a mesma célula.**
    6. **A marcação `erro` é DURÁVEL** (rework da 09.3) — `commit()` antes do
       `raise`, e na ordem certa em relação à auditoria.
    7. **A rota de lançamentos não constrói client fora da fábrica** (rework da
       09.6) — o defeito morava na ROTA, não no serviço, e é lá que esta guarda
       bate.
    8. **O `except` do laço de conversão não lê atributo do objeto sujo**
       (rework #2 da 09.5) — depois do rollback do SAVEPOINT o `client` está
       EXPIRADO, e ler `client.id` ali é IO: sob asyncio vira `MissingGreenlet`
       de DENTRO do tratador, o `commit()` do lote nunca roda e o lote se perde.
    9. **Toda URL literal dos testes da sprint existe no app** (rework #2 da
       09.6) — e a bateria do 409 não guarda path nenhum com asserção frouxa.

Os blocos 6 a 9 existem porque as provas que os executores escreveram para o
retrabalho são de INTEGRAÇÃO (Postgres) ou de serviço: nenhuma roda sem Docker,
e os defeitos da 09.6 estavam uma camada acima do que o teste deles alcança —
o do rework #2, aliás, estava DENTRO do próprio teste (path inexistente).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.authz import PERMISSION_MATRIX, Permission, UserRole
from app.db.models.client_connection import ClientConnection, ConnectionStatus, ProviderType
from app.integrations.providers.base import Capability
from app.modules.client_connections import schemas as connection_schemas
from app.modules.client_connections.capability import connection_supports, select_capable_connection
from app.modules.client_connections.legacy_fallback import (
    is_synthetic,
    synthesize_legacy_connection,
)
from app.modules.clients.schemas import OriginStatus, derive_origin_status

#: Qualquer campo cujo nome contenha um destes é credencial. Substring e não
#: igualdade: o vazamento provável não se chama `credentials`, chama-se
#: `app_key_masked`, `credentials_preview`, `secret_hint`.
_CREDENTIAL_MARKERS = ("credential", "secret", "app_key", "appkey", "password", "token", "_iv")

#: Os schemas que SAEM pela API do módulo. Os de ENTRADA (`Create...Request`,
#: `Update...Request`) recebem credencial de propósito e ficam fora.
_RESPONSE_SCHEMA_NAMES = (
    "ClientConnectionResponse",
    "ClientConnectionEnvelope",
    "ClientConnectionListPayload",
    "ClientConnectionListResponse",
    "ConnectionDeletedPayload",
    "ConnectionDeletedResponse",
)


def _connection(
    *,
    status: ConnectionStatus = ConnectionStatus.ATIVA,
    provider_type: str = ProviderType.OMIE.value,
    label: str = "Omie",
) -> ClientConnection:
    """Linha em memória. Nunca vai para sessão nenhuma — é teste de regra pura."""
    return ClientConnection(
        id=uuid4(),
        client_id=uuid4(),
        provider_type=provider_type,
        label=label,
        status=status.value,
    )


class TestNenhumaRespostaCarregaCredencial:
    """A afirmação central da sprint, travada no nome dos campos."""

    @pytest.mark.parametrize("schema_name", _RESPONSE_SCHEMA_NAMES)
    def test_schema_de_resposta_nao_declara_campo_de_credencial(self, schema_name: str) -> None:
        model = getattr(connection_schemas, schema_name)
        suspeitos = [
            field
            for field in model.model_fields
            if any(marker in field.lower() for marker in _CREDENTIAL_MARKERS)
        ]
        assert suspeitos == [], (
            f"{schema_name} declara {suspeitos}: credencial NÃO sai da API, "
            "nem mascarada (§3.2). Quem precisa dela é o adaptador, dentro do servidor."
        )

    def test_a_lista_de_schemas_varridos_esta_completa(self) -> None:
        """Schema de resposta novo sem entrar na varredura é buraco silencioso.

        Sem esta trava, acrescentar `ClientConnectionCredentialsResponse` passaria
        pelo teste acima por simples OMISSÃO — o pior modo de um guardião falhar.
        """
        declarados = {
            name
            for name in dir(connection_schemas)
            if name.endswith(("Response", "Envelope", "Payload")) and not name.startswith("_")
        }
        # Os de entrada terminam em `Request` e por isso não aparecem aqui.
        assert declarados == set(_RESPONSE_SCHEMA_NAMES), (
            "Schema de saída novo no módulo de conexões: acrescente-o a "
            "_RESPONSE_SCHEMA_NAMES para que a varredura de credencial o alcance."
        )

    def test_o_dto_de_resposta_expoe_exatamente_o_combinado(self) -> None:
        """Nem a mais, nem a menos — o front tipa contra estes seis campos."""
        assert set(connection_schemas.ClientConnectionResponse.model_fields) == {
            "id",
            "provider_type",
            "label",
            "status",
            "last_checked_at",
            "accounts_synced_at",
            "capabilities",
        }


class TestDerivacaoDoEstadoDeOrigem:
    """`origin_status` é DERIVADO — e a tabela-verdade inteira cabe aqui."""

    @pytest.mark.parametrize(
        ("total", "active", "esperado"),
        [
            (0, 0, OriginStatus.SEM_ORIGEM),
            (1, 1, OriginStatus.ATIVA),
            (3, 1, OriginStatus.ATIVA),
            (1, 0, OriginStatus.ERRO),
            (2, 0, OriginStatus.ERRO),
        ],
    )
    def test_tabela_verdade(self, total: int, active: int, esperado: OriginStatus) -> None:
        assert derive_origin_status(total=total, active=active) is esperado

    def test_os_tres_estados_e_so_eles(self) -> None:
        """Estado novo é decisão de produto — não pode entrar sem passar por aqui."""
        assert {s.value for s in OriginStatus} == {"sem_origem", "ativa", "erro"}


class TestTaxonomiaDosTresNoves:
    """Cada situação devolve o SEU código — é o que a tela usa para dar o remédio."""

    def test_sem_conexao_nenhuma(self) -> None:
        from app.core.exceptions import NoOriginConnectionError

        with pytest.raises(NoOriginConnectionError):
            select_capable_connection([], Capability.LISTAR_CONTAS)

    def test_existe_mas_nenhuma_ativa(self) -> None:
        from app.core.exceptions import OriginConnectionInErrorError

        with pytest.raises(OriginConnectionInErrorError):
            select_capable_connection(
                [_connection(status=ConnectionStatus.ERRO)], Capability.LISTAR_CONTAS
            )

    def test_inativa_nao_e_capaz(self) -> None:
        """Desligada de propósito também não serve — e não vira `sem_conexao`."""
        assert not connection_supports(
            _connection(status=ConnectionStatus.INATIVA), Capability.LISTAR_CONTAS
        )

    def test_ativa_e_capaz_e_escolhida(self) -> None:
        ativa = _connection()
        assert select_capable_connection([ativa], Capability.LISTAR_CONTAS) is ativa


class TestConexaoSintetizadaDaJanelaDeConversao:
    """O fallback da 09.5 não pode criar uma segunda cópia do segredo."""

    def test_sintetizada_nao_carrega_credencial(self) -> None:
        from app.db.models.client import Client

        client = Client(id=uuid4(), name="Legado", active=True, created_by=uuid4())
        sintetizada = synthesize_legacy_connection(client)
        assert sintetizada.credentials_encrypted is None
        assert sintetizada.credentials_iv is None

    def test_o_id_e_deterministico_e_reconhecivel(self) -> None:
        """Mesmo cliente, mesmo id — senão log e telemetria veem conexão nova a cada request."""
        from app.db.models.client import Client

        client = Client(id=uuid4(), name="Legado", active=True, created_by=uuid4())
        primeira = synthesize_legacy_connection(client)
        segunda = synthesize_legacy_connection(client)
        assert primeira.id == segunda.id
        assert is_synthetic(primeira)

    def test_conexao_real_nao_e_confundida_com_sintetizada(self) -> None:
        assert not is_synthetic(_connection())


class TestPermissaoNovaNaMatriz:
    """A célula do R5: staff sim, papéis de cliente não."""

    def test_staff_gerencia_conexoes(self) -> None:
        permitidos = PERMISSION_MATRIX[Permission.MANAGE_CLIENT_CONNECTIONS]
        assert UserRole.PLATFORM_ADMIN in permitidos
        assert UserRole.ADMIN in permitidos
        assert UserRole.MANAGER in permitidos

    def test_papeis_de_cliente_nao_gerenciam_conexoes(self) -> None:
        """Credencial de sistema contábil é configuração do escritório."""
        permitidos = PERMISSION_MATRIX[Permission.MANAGE_CLIENT_CONNECTIONS]
        assert UserRole.CLIENT_MANAGER not in permitidos
        assert UserRole.CLIENT_OPERATOR not in permitidos


class TestDurabilidadeDaMarcacaoDeErro:
    """Rework da 09.3 — `flush()` não sobrevive ao `rollback()` da request.

    A prova que o backend escreveu é de INTEGRAÇÃO (fixture com a política real
    de transação) e não roda sem Postgres. Esta aqui roda sempre: monta o
    serviço real com uma sessão de mentira e observa a ORDEM das chamadas.
    Se alguém trocar o `commit()` de volta por `flush()`, ou marcar o erro
    depois do `raise`, cai aqui — sem banco, sem rede.
    """

    @staticmethod
    def _montar() -> tuple[object, list[str]]:
        from app.core.exceptions import ProviderAuthError
        from app.db.models.client import Client
        from app.modules.client_connections.service import ClientConnectionService

        ordem: list[str] = []
        conexao = _connection()

        class _Db:
            async def commit(self) -> None:
                ordem.append("commit")

            async def flush(self) -> None:
                ordem.append("flush")

            async def refresh(self, _obj: object) -> None:  # pragma: no cover - não usado
                ordem.append("refresh")

        class _Repo:
            async def get_in_client(self, _id: object, *, client_id: object) -> ClientConnection:
                return conexao

            async def mark_connection_error(self, _id: object) -> None:
                ordem.append("mark_erro")

            async def mark_connection_checked(self, _id: object) -> None:  # pragma: no cover
                ordem.append("mark_ok")

        service = ClientConnectionService(_Db(), object())  # type: ignore[arg-type]
        service._repo = _Repo()  # type: ignore[assignment]

        async def _decrypt(_client: object, _conn: object) -> dict[str, object]:
            return {}

        async def _verify(_provider_type: str, _credentials: object) -> None:
            raise ProviderAuthError("credencial recusada pelo provedor")

        async def _audit(_user: object, _client: object, _action: object) -> None:
            ordem.append("audit")

        service._decrypt_credentials = _decrypt  # type: ignore[assignment]
        service._verify_against_provider = _verify  # type: ignore[assignment]
        service._audit = _audit  # type: ignore[assignment]

        client = Client(id=conexao.client_id, name="Cliente", active=True, created_by=uuid4())
        return (service, client, conexao), ordem  # type: ignore[return-value]

    async def test_credencial_recusada_commita_antes_de_re_levantar(self) -> None:
        """A barreira de durabilidade: `mark_erro` → `audit` → `commit` → `raise`.

        Sem o `commit()`, o `raise` sobe até `get_db_session`
        (`app/db/session.py`), que faz `rollback()`: a conexão voltaria para
        `ativa`, a tela nunca ofereceria "reconectar" e a linha de negação em
        `access_audit` sumiria junto (§4.7).
        """
        from app.core.exceptions import ProviderAuthError

        (service, client, conexao), ordem = self._montar()

        with pytest.raises(ProviderAuthError):
            await service.test_connection(  # type: ignore[attr-defined]
                client=client, user=object(), connection_id=conexao.id
            )

        assert "commit" in ordem, (
            "test_connection não commitou antes de re-levantar: a marcação `erro` "
            "e a auditoria morrem no rollback da request (app/db/session.py)."
        )
        assert ordem.index("mark_erro") < ordem.index("commit")
        assert ordem.index("audit") < ordem.index("commit")
        assert ordem[-1] == "commit", f"o commit tem de ser a última escrita; veio {ordem}"


class TestRotaDeLancamentosResolveOrigemDENTRODaFabrica:
    """Rework da 09.6 — o defeito morava na ROTA, e é nela que esta guarda bate.

    O teste de serviço do backend prova que a fábrica só é invocada no MISS. Ele
    não prova o que a reprovação apontava: a ROTA construía o `OmieClient` FORA
    e passava `lambda: omie_client`, então em cache HIT o client nascia,
    ninguém o fechava, o unwrap da DEK ia ao KMS em toda request e o 409 de
    origem estourava onde o cache resolveria.

    Aqui o serviço é substituído por um dublê que NUNCA invoca a fábrica (o
    cache hit total) e `build_capable_client` conta invocações: zero é a única
    resposta aceitável. E para a fábrica não ser um enfeite morto, o teste
    seguinte a invoca e exige que ela construa.
    """

    @staticmethod
    def _preparar(monkeypatch: pytest.MonkeyPatch, *, invocar_fabrica: bool) -> dict[str, object]:
        from types import SimpleNamespace

        from app.modules.omie_data import routes as rotas

        estado: dict[str, object] = {"construidos": 0, "fabrica_e_async": None}

        async def _fake_build_capable_client(*_args: object, **_kwargs: object) -> object:
            estado["construidos"] = int(estado["construidos"]) + 1  # type: ignore[arg-type]
            return SimpleNamespace(aclose=lambda: None)

        async def _fake_require_session_access(*_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(client_id=uuid4())

        class _ServicoDuble:
            def __init__(self, *_args: object, **_kwargs: object) -> None: ...

            async def fetch_lancamentos(
                self, *, session_id: object, omie_ids: object, omie_client_factory: object
            ) -> list[object]:
                import inspect

                estado["fabrica_e_async"] = inspect.iscoroutinefunction(omie_client_factory)
                if invocar_fabrica:  # simula o MISS
                    await omie_client_factory()  # type: ignore[operator]
                return []

        class _Db:
            async def execute(self, *_args: object, **_kwargs: object) -> object:
                from app.db.models.client import Client

                client = Client(id=uuid4(), name="Cliente", active=True, created_by=uuid4())
                return SimpleNamespace(scalar_one_or_none=lambda: client)

        monkeypatch.setattr(rotas, "build_capable_client", _fake_build_capable_client)
        monkeypatch.setattr(rotas, "require_session_access", _fake_require_session_access)
        monkeypatch.setattr(rotas, "OmieLancamentoService", _ServicoDuble)
        monkeypatch.setattr(rotas, "ReviewRepository", lambda _db: object())

        estado["db"] = _Db()
        estado["request"] = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(omie_lancamento_cache=object()))
        )
        return estado

    async def test_cache_hit_nao_resolve_origem_nem_constroi_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.modules.omie_data.routes import get_omie_lancamentos

        estado = self._preparar(monkeypatch, invocar_fabrica=False)
        await get_omie_lancamentos(
            user=object(),  # type: ignore[arg-type]
            db=estado["db"],  # type: ignore[arg-type]
            request=estado["request"],  # type: ignore[arg-type]
            settings=object(),  # type: ignore[arg-type]
            ids="1,2",
            session_id=uuid4(),
        )

        assert estado["construidos"] == 0, (
            "a rota resolveu a origem fora da fábrica: em cache hit isso vaza um "
            "httpx.AsyncClient, paga unwrap de DEK no KMS e pode devolver 409 "
            "numa request que o cache resolveria."
        )
        assert estado["fabrica_e_async"] is True, (
            "a fábrica tem de ser `async` — é o contrato que o serviço aguarda "
            "(`Callable[[], Awaitable[OmieClient]]`)."
        )

    async def test_no_miss_a_fabrica_realmente_constroi(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Contraprova: a fábrica não pode ser um enfeite que nunca constrói."""
        from app.modules.omie_data.routes import get_omie_lancamentos

        estado = self._preparar(monkeypatch, invocar_fabrica=True)
        await get_omie_lancamentos(
            user=object(),  # type: ignore[arg-type]
            db=estado["db"],  # type: ignore[arg-type]
            request=estado["request"],  # type: ignore[arg-type]
            settings=object(),  # type: ignore[arg-type]
            ids="1,2",
            session_id=uuid4(),
        )
        assert estado["construidos"] == 1


# --------------------------------------------------------------------------- #
# 8. O `except` do laço de conversão não pode tocar o objeto sujo (09.5 / R2)
# --------------------------------------------------------------------------- #

#: Raiz de `apps/api`, a partir deste arquivo (`tests/unit/<este>.py`).
_API_ROOT = Path(__file__).resolve().parents[2]
_CONVERSION_SCRIPT = _API_ROOT / "scripts" / "convert_credentials_to_connections.py"


class TestExceptDoLoteNaoLeObjetoExpirado:
    """Guarda estática porque o defeito só aparece com Postgres + asyncio.

    `SessionTransaction._restore_snapshot(dirty_only=True)` expira todo estado
    `modified`/`_dirty` no rollback do SAVEPOINT. `_convert_one` suja a linha
    (`provision_client_cipher` seta `dek_wrapped` in-place no cliente *bare*,
    que é o legado típico), então quando o `except` roda o `client` está
    expirado: `client.id` deixa de ser leitura de memória e vira SELECT, que
    sob asyncio levanta `MissingGreenlet` de dentro do próprio tratador. A
    exceção escapa do `except`, o `await db.commit()` do lote nunca roda, e a
    propriedade escrita no cabeçalho do script ("falha permanente numa linha
    não derruba o lote") passa a ser falsa.

    O teste que o executor escreveu para isto (`test_falha_no_meio_do_lote…`)
    é de integração e usa justamente um cliente *bare* como linha podre — ou
    seja, ele encontraria o defeito, mas só numa máquina com Docker. Esta
    guarda custa zero e roda sempre.
    """

    @staticmethod
    def _laco_do_lote() -> ast.For:
        """O `for client in pending:` de dentro de `run_conversion`."""
        arvore = ast.parse(_CONVERSION_SCRIPT.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if isinstance(no, ast.AsyncFunctionDef) and no.name == "run_conversion":
                for interno in ast.walk(no):
                    if (
                        isinstance(interno, ast.For)
                        and isinstance(interno.target, ast.Name)
                        and interno.target.id == "client"
                    ):
                        return interno
        pytest.fail("não achei o laço `for client in pending:` em `run_conversion`")

    def test_nenhum_handler_le_atributo_do_client(self) -> None:
        laco = self._laco_do_lote()
        lidos = [
            f"client.{no.attr} (linha {no.lineno})"
            for tentativa in ast.walk(laco)
            if isinstance(tentativa, ast.Try)
            for handler in tentativa.handlers
            for no in ast.walk(handler)
            if isinstance(no, ast.Attribute)
            and isinstance(no.value, ast.Name)
            and no.value.id == "client"
        ]
        assert not lidos, (
            "o tratador de erro do lote lê atributo do objeto que o rollback "
            f"acabou de expirar: {', '.join(lidos)}. Sob asyncio isso é "
            "`MissingGreenlet` DENTRO do `except` — a exceção escapa, o "
            "`commit()` do lote nunca roda e o lote inteiro se perde sem "
            "relatório. Capture o escalar ANTES do bloco transacional."
        )

    def test_o_escalar_e_capturado_antes_do_bloco_transacional(self) -> None:
        """Contraprova: não basta não ler — o id tem de existir de outro jeito.

        Sem esta metade, apagar as três linhas do `except` passaria a guarda
        acima e destruiria o relatório de falhas.
        """
        laco = self._laco_do_lote()
        primeiro_try = next(
            (i for i, no in enumerate(laco.body) if isinstance(no, ast.Try)),
            len(laco.body),
        )
        captura = [
            no
            for no in laco.body[:primeiro_try]
            if isinstance(no, ast.Assign)
            and any(isinstance(alvo, ast.Name) and alvo.id == "client_id" for alvo in no.targets)
        ]
        assert captura, (
            "`client_id` tem de ser capturado ANTES do `try`/`begin_nested` — "
            "é a única leitura de `client.id` que acontece com o objeto ainda "
            "vivo."
        )

        usado_no_handler = any(
            isinstance(no, ast.Name) and no.id == "client_id"
            for tentativa in ast.walk(laco)
            if isinstance(tentativa, ast.Try)
            for handler in tentativa.handlers
            for no in ast.walk(handler)
        )
        assert usado_no_handler, (
            "`client_id` é capturado e o `except` não o usa: o relatório de "
            "falhas (`failed_client_ids`) perdeu a evidência operacional."
        )


# --------------------------------------------------------------------------- #
# 9. URL de teste se confere contra o app, não contra o vizinho (09.6 / R2)
# --------------------------------------------------------------------------- #

#: Os arquivos que a Sprint 9 criou/alterou e que batem em rota por URL literal.
_ARQUIVOS_DE_ROTA_DA_SPRINT = (
    "tests/integration/test_origin_required_routes.py",
    "tests/integration/test_client_connections.py",
    "tests/integration/test_convert_credentials_to_connections.py",
)


class TestURLDeTesteExisteNoApp:
    """Asserção frouxa não distingue "rota respondeu" de "rota não existe".

    O caso `test_categorias_do_omie` chamava `/api/v1/omie-data/categorias`
    desde a rodada 1 — path que nunca existiu (o router é
    `prefix="/api/v1/omie"`). Como a asserção dele era `status_code < 500`, o
    **404 passava verde**: o cenário jamais foi exercitado. Na rodada 2 o teste
    do rework da 09.6 foi escrito copiando esse vizinho — mesmo prefixo errado,
    mesmo `sessionId` em vez de `session_id` — só que com `== 200`, então ele
    **falharia** assim que alguém subisse o Postgres.

    Esta guarda casa cada URL literal contra as rotas REAIS do app, sem subir
    servidor nem banco. `{...}` (path param do app ou interpolação de f-string
    no teste) vira curinga dos dois lados.
    """

    @staticmethod
    def _padroes_de_rota() -> list[re.Pattern[str]]:
        from app.main import app

        padroes = []
        for rota in app.routes:
            caminho = getattr(rota, "path", None)
            if not caminho:
                continue
            corpo = re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(caminho))
            padroes.append(re.compile(f"^{corpo}$"))
        return padroes

    @pytest.mark.parametrize("relativo", _ARQUIVOS_DE_ROTA_DA_SPRINT)
    def test_toda_url_literal_bate_com_uma_rota_real(self, relativo: str) -> None:
        caminho = _API_ROOT / relativo
        if not caminho.exists():  # pragma: no cover - arquivo renomeado
            pytest.skip(f"{relativo} não existe neste worktree")

        padroes = self._padroes_de_rota()
        fonte = caminho.read_text(encoding="utf-8")
        # Só chamadas: `client.get("/api/v1/...")`. A varredura crua pegaria
        # também os paths citados em docstring e comentário — e foi um
        # COMENTÁRIO explicando o prefixo certo que apareceu no rework.
        urls = {
            achado
            for linha in fonte.splitlines()
            if not linha.lstrip().startswith("#")
            for achado in re.findall(r"""['"](/api/v1/[^'"\s]*)['"]""", linha)
        }

        orfas = sorted(
            url
            for url in urls
            if not any(
                padrao.match(re.sub(r"\{[^}]*\}", "X", url.split("?")[0])) for padrao in padroes
            )
        )
        assert not orfas, (
            f"{relativo} chama path que não existe no app: {orfas}. "
            "404 não é o cenário que o teste afirma testar — e com asserção "
            "frouxa ele ainda passa verde e vira molde para o próximo."
        )

    def test_o_caso_que_o_rework_consertou_afirma_o_409(self) -> None:
        """Contraprova pontual: o caso das categorias não pode voltar a ser `< 500`.

        Note o que esta guarda NÃO faz: proibir `status_code < 500` no arquivo
        inteiro. Sobram três casos com ele, e são deliberados — em
        `POST /reconciliations` a validação do payload (422) chega antes do 409,
        e o caso tem uma segunda asserção de verdade (nenhuma sessão gravada).
        Banir a asserção frouxa em bloco reprovaria esses três sem defeito
        nenhum. O que tornava `< 500` perigoso era guardar um path SOZINHA, e
        isso já morreu no teste acima: nenhum path do arquivo pode não existir.
        """
        caminho = _API_ROOT / "tests/integration/test_origin_required_routes.py"
        if not caminho.exists():  # pragma: no cover
            pytest.skip("arquivo renomeado")

        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        caso = next(
            (
                no
                for no in ast.walk(arvore)
                if isinstance(no, ast.AsyncFunctionDef) and no.name == "test_categorias_do_omie"
            ),
            None,
        )
        assert caso is not None, "o caso `test_categorias_do_omie` sumiu da bateria"

        afirma_409 = any(
            isinstance(no, ast.Call)
            and isinstance(no.func, ast.Name)
            and no.func.id == "_assert_origin_409"
            for no in ast.walk(caso)
        )
        assert afirma_409, (
            "`test_categorias_do_omie` voltou a não afirmar o 409 da taxonomia. "
            "Foi o caso cuja asserção `< 500` engoliu um 404 por duas rodadas — "
            "o cenário nunca rodou de verdade e ainda virou molde para o teste "
            "do rework, que copiou o path errado dele."
        )
