"""Lista CANÔNICA de endpoints sensíveis a tenant (Sprint 5 / R3 — BACK 05.4).

É o **denominador fechado** da métrica da sprint ("endpoints sensíveis com caso
negativo cross-tenant testado e passando ÷ total"). Sem esta lista, "100%" é um
número sobre um conjunto arbitrário e inverificável.

Endpoint sensível = rota que **lê ou muta dado escopável a um cliente/tenant OU
a uma organização** (camada de organizações, 86e36ecnp: cross-org é o
cross-tenant uma camada acima), tanto por coleção quanto por PK do recurso.
Rotas de autenticação, de configuração global (tipos de anomalia), de
administração da plataforma (`/organizations`, só `platform_admin`) e o
alert-test **não** entram: não carregam dado de cliente nem de organização.

Cada entrada aponta para o path REAL e o módulo que o implementa — verificados
contra `app.routes` por `tests/integration/test_sensitive_endpoints.py`, que
também falha quando um endpoint novo com `{client_id}`/`{session_id}` aparece
sem ser registrado aqui. A lista não é decorativa: ela quebra o CI quando fica
desatualizada.

Companion legível: `apps/api/docs/endpoints-sensiveis-sprint5.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ScopeKind(StrEnum):
    """Como o recurso é endereçado — muda o modo de vazar."""

    #: Lista/agregado. Vaza forjando `client_id` na URL/payload.
    COLLECTION = "collection"
    #: Recurso por PK, **sem** `client_id` na requisição. O mais fácil de
    #: esquecer: não há nada no request para filtrar — o filtro tem de vir da
    #: linha do usuário, dentro do SELECT.
    DETAIL_PK = "detail_pk"


@dataclass(frozen=True)
class SensitiveEndpoint:
    """Uma rota do denominador da métrica."""

    method: str
    path: str
    kind: ScopeKind
    #: Arquivo (relativo a `apps/api/`) que implementa a rota.
    module: str
    #: Como o tenant é imposto — em uma linha, verificável no código.
    mechanism: str

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


#: Mecanismos recorrentes — nomeados para não repetir a frase em 20 linhas.
_VIA_STAFF_ORG = (
    "ManageOrgUsersDep + get_staff_by_id/scoped_by_organization: AND scope='system' AND "
    "organization_id = <org do observador> no próprio SELECT (plataforma: todas); "
    "plataforma, usuário de cliente e staff de outra org = 404"
)
_VIA_CATEGORY_ORG = (
    "StaffDep/ManageClientCategoriesDep + scoped_by_organization no SELECT do catálogo "
    "(AND organization_id = <org do observador>; plataforma: todas); alvo por PK de outra "
    "organização = 404; a categoria nova nasce na org da LINHA do ator "
    "(resolve_organization_for_creation)"
)
_VIA_SESSION = (
    "require_session_access: SELECT da sessão já com AND client_id = <tenant da "
    "linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme"
)
_VIA_CLIENT_PATH = (
    "AccessibleClientDep -> require_client_access -> resolve_client_access "
    "(o client_id do path só passa se for o tenant da linha)"
)

SENSITIVE_ENDPOINTS: tuple[SensitiveEndpoint, ...] = (
    # --------------------------------------- organizações (86e36ecnp): alcance por org
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients",
        ScopeKind.COLLECTION,
        "app/modules/clients/routes.py",
        "StaffDep + reach_filter no SELECT (plataforma tudo; admin a própria org; "
        "manager a carteira dentro dela; cliente = 403)",
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients",
        ScopeKind.COLLECTION,
        "app/modules/clients/routes.py",
        "CreateClientDep; a org do cliente novo vem da LINHA do ator (staff só a "
        "própria: divergente = 403; plataforma escolhe, validada)",
    ),
    SensitiveEndpoint(
        "PATCH",
        "/api/v1/clients/{client_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/clients/routes.py",
        _VIA_CLIENT_PATH + " + EditClientDep",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/users",
        ScopeKind.COLLECTION,
        "app/modules/users/routes.py",
        _VIA_STAFF_ORG,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/users",
        ScopeKind.COLLECTION,
        "app/modules/users/routes.py",
        "ManageOrgUsersDep; a org do staff novo é carimbada da LINHA do ator",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/users/{user_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/users/routes.py",
        _VIA_STAFF_ORG,
    ),
    SensitiveEndpoint(
        "PATCH",
        "/api/v1/users/{user_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/users/routes.py",
        _VIA_STAFF_ORG,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/users/{user_id}/activate",
        ScopeKind.DETAIL_PK,
        "app/modules/users/routes.py",
        _VIA_STAFF_ORG,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/users/{user_id}/deactivate",
        ScopeKind.DETAIL_PK,
        "app/modules/users/routes.py",
        _VIA_STAFF_ORG,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/users/{user_id}/transfer",
        ScopeKind.DETAIL_PK,
        "app/modules/users/routes.py",
        "ManagePlatformDep (só plataforma; admin e gerente de QUALQUER organização = 403 "
        "antes de tocar a linha) + get_staff_by_id: alvo só staff (scope='system'), "
        "usuário de cliente e plataforma = 404",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/client-categories",
        ScopeKind.COLLECTION,
        "app/modules/client_categories/routes.py",
        _VIA_CATEGORY_ORG,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/client-categories",
        ScopeKind.COLLECTION,
        "app/modules/client_categories/routes.py",
        _VIA_CATEGORY_ORG,
    ),
    SensitiveEndpoint(
        "PATCH",
        "/api/v1/client-categories/{category_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/client_categories/routes.py",
        _VIA_CATEGORY_ORG,
    ),
    SensitiveEndpoint(
        "DELETE",
        "/api/v1/client-categories/{category_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/client_categories/routes.py",
        _VIA_CATEGORY_ORG,
    ),
    # ---------------------------------------------------------------- conciliações
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/reconciliations",
        ScopeKind.COLLECTION,
        "app/modules/clients/routes.py",
        _VIA_CLIENT_PATH,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/reconciliations",
        ScopeKind.COLLECTION,
        "app/modules/reconciliations/routes.py",
        "client_id do body validado por require_client_access antes de criar",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/reconciliations/check-duplicate",
        ScopeKind.COLLECTION,
        "app/modules/reconciliations/routes.py",
        "client_id da query validado por require_client_access",
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/reconciliations/parse",
        ScopeKind.COLLECTION,
        "app/modules/reconciliations/routes.py",
        "client_id do body validado por require_client_access",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/reconciliations/{session_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/reconciliations/routes.py",
        _VIA_SESSION,
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/reconciliations/{session_id}/status",
        ScopeKind.DETAIL_PK,
        "app/modules/reconciliations/routes.py",
        _VIA_SESSION,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/reconciliations/{session_id}/reprocess",
        ScopeKind.DETAIL_PK,
        "app/modules/reconciliations/routes.py",
        _VIA_SESSION,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/reconciliations/{session_id}/cancel",
        ScopeKind.DETAIL_PK,
        "app/modules/reconciliations/routes.py",
        _VIA_SESSION,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/reconciliations/{session_id}/discard",
        ScopeKind.DETAIL_PK,
        "app/modules/reconciliations/routes.py",
        _VIA_SESSION,
    ),
    # ---------------------------------------------------------------- anomalias
    SensitiveEndpoint(
        "GET",
        "/api/v1/reconciliations/{session_id}/anomalies",
        ScopeKind.COLLECTION,
        "app/modules/reconciliations/review/routes.py",
        _VIA_SESSION,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/reconciliations/{session_id}/anomalies",
        ScopeKind.COLLECTION,
        "app/modules/reconciliations/review/routes.py",
        _VIA_SESSION,
    ),
    SensitiveEndpoint(
        "PATCH",
        "/api/v1/reconciliations/{session_id}/anomalies/{anomaly_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/reconciliations/review/routes.py",
        f"{_VIA_SESSION}; e get_anomaly filtra AND session_id",
    ),
    # ---------------------------------------------------------------- arquivos
    SensitiveEndpoint(
        "GET",
        "/api/v1/reconciliations/{session_id}/files",
        ScopeKind.COLLECTION,
        "app/modules/reconciliations/routes.py",
        _VIA_SESSION,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/reconciliations/{session_id}/files",
        ScopeKind.COLLECTION,
        "app/modules/reconciliations/routes.py",
        _VIA_SESSION,
    ),
    SensitiveEndpoint(
        "DELETE",
        "/api/v1/reconciliations/{session_id}/files/{file_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/reconciliations/routes.py",
        f"{_VIA_SESSION}; e get_file filtra AND session_id",
    ),
    # ---------------------------------------------------------------- linhas revisadas
    SensitiveEndpoint(
        "GET",
        "/api/v1/reconciliations/{session_id}/file-entries",
        ScopeKind.COLLECTION,
        "app/modules/reconciliations/review/routes.py",
        _VIA_SESSION,
    ),
    SensitiveEndpoint(
        "PATCH",
        "/api/v1/reconciliations/{session_id}/file-entries/{entry_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/reconciliations/review/routes.py",
        f"{_VIA_SESSION}; e get_file_entry filtra AND session_id",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/reconciliations/{session_id}/omie-entries",
        ScopeKind.COLLECTION,
        "app/modules/reconciliations/review/routes.py",
        _VIA_SESSION,
    ),
    SensitiveEndpoint(
        "PATCH",
        "/api/v1/reconciliations/{session_id}/omie-entries/{entry_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/reconciliations/review/routes.py",
        f"{_VIA_SESSION}; e get_omie_entry filtra AND session_id",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/reconciliations/{session_id}/available-omie-entries",
        ScopeKind.COLLECTION,
        "app/modules/reconciliations/review/routes.py",
        _VIA_SESSION,
    ),
    # ---------------------------------------------------------------- contas bancárias
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/clients/routes.py",
        _VIA_CLIENT_PATH,
    ),
    SensitiveEndpoint(
        "PATCH",
        "/api/v1/clients/{client_id}/sync-accounts",
        ScopeKind.DETAIL_PK,
        "app/modules/clients/routes.py",
        _VIA_CLIENT_PATH,
    ),
    # ---------------------------------------------------------------- exclusão (86e34jd1d)
    SensitiveEndpoint(
        "DELETE",
        "/api/v1/clients/{client_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/clients/routes.py",
        "EditClientDep (admin pela matriz) + " + _VIA_CLIENT_PATH,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/close",
        ScopeKind.DETAIL_PK,
        "app/modules/clients/routes.py",
        "EditClientDep (admin pela matriz) + " + _VIA_CLIENT_PATH,
    ),
    # ---------------------------------------------------------------- favoritos (86e34jd5a)
    SensitiveEndpoint(
        "PUT",
        "/api/v1/clients/{client_id}/favorite",
        ScopeKind.DETAIL_PK,
        "app/modules/clients/routes.py",
        _VIA_CLIENT_PATH,
    ),
    SensitiveEndpoint(
        "DELETE",
        "/api/v1/clients/{client_id}/favorite",
        ScopeKind.DETAIL_PK,
        "app/modules/clients/routes.py",
        _VIA_CLIENT_PATH,
    ),
    # ---------------------------------------------------------------- carteira (86e390kz8)
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/managers",
        ScopeKind.DETAIL_PK,
        "app/modules/clients/routes.py",
        "EditClientDep (admin pela matriz) + " + _VIA_CLIENT_PATH,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/managers",
        ScopeKind.DETAIL_PK,
        "app/modules/clients/routes.py",
        "EditClientDep (admin pela matriz) + OpenClientDep (encerrado = 409) + " + _VIA_CLIENT_PATH,
    ),
    SensitiveEndpoint(
        "DELETE",
        "/api/v1/clients/{client_id}/managers/{user_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/clients/routes.py",
        "EditClientDep (admin pela matriz) + OpenClientDep (encerrado = 409) + " + _VIA_CLIENT_PATH,
    ),
    SensitiveEndpoint(
        "PATCH",
        "/api/v1/clients/{client_id}/assign",
        ScopeKind.DETAIL_PK,
        "app/modules/clients/routes.py",
        "EditClientDep (admin pela matriz) + OpenClientDep (encerrado = 409) + " + _VIA_CLIENT_PATH,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/reconciliations/{session_id}/omie-postings",
        ScopeKind.DETAIL_PK,
        "app/modules/reconciliations/routes.py",
        (
            f"{_VIA_SESSION}; o Client sai do client_id da sessão já autorizada e "
            "as linhas são carregadas com AND session_id — nada vem do body"
        ),
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/omie/lancamentos",
        ScopeKind.COLLECTION,
        "app/modules/omie_data/routes.py",
        _VIA_SESSION,
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/omie/categorias",
        ScopeKind.COLLECTION,
        "app/modules/omie_data/routes.py",
        f"{_VIA_SESSION}; o client_id do cache vem da sessão, nunca da query",
    ),
    # ---------------------------------------------------------------- notificações
    SensitiveEndpoint(
        "GET",
        "/api/v1/notifications",
        ScopeKind.COLLECTION,
        "app/modules/notifications/repository.py",
        "_visibility_filter: user_id = eu AND client_id = <tenant da linha>",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/notifications/unread-count",
        ScopeKind.COLLECTION,
        "app/modules/notifications/repository.py",
        "_visibility_filter: user_id = eu AND client_id = <tenant da linha>",
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/notifications/read-all",
        ScopeKind.COLLECTION,
        "app/modules/notifications/repository.py",
        "mark_all_read com o mesmo _visibility_filter no UPDATE",
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/notifications/{notification_id}/read",
        ScopeKind.DETAIL_PK,
        "app/modules/notifications/repository.py",
        "get_for_user com o mesmo _visibility_filter; 404 uniforme",
    ),
    # ---------------------------------------------------------------- exportação
    SensitiveEndpoint(
        "POST",
        "/api/v1/reconciliations/{session_id}/export",
        ScopeKind.DETAIL_PK,
        "app/modules/reconciliations/export/routes.py",
        _VIA_SESSION,
    ),
    # ---------------------------------------------------------------- instrumentação
    SensitiveEndpoint(
        "POST",
        "/api/v1/usage-events",
        ScopeKind.DETAIL_PK,
        "app/modules/usage_events/repository.py",
        "get_session_client_id com scoped_by_tenant; sessão alheia vira 404",
    ),
    # ---------------------------------------------------------------- usuários do cliente
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/users",
        ScopeKind.COLLECTION,
        "app/modules/users/client_routes.py",
        _VIA_CLIENT_PATH,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/users",
        ScopeKind.COLLECTION,
        "app/modules/users/client_routes.py",
        f"{_VIA_CLIENT_PATH}; client_id do novo usuário fixado pelo servidor",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/users/{user_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/users/client_routes.py",
        f"{_VIA_CLIENT_PATH}; SELECT do alvo com AND client_id (anti-IDOR)",
    ),
    SensitiveEndpoint(
        "PATCH",
        "/api/v1/clients/{client_id}/users/{user_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/users/client_routes.py",
        f"{_VIA_CLIENT_PATH}; SELECT do alvo com AND client_id (anti-IDOR)",
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/users/{user_id}/activate",
        ScopeKind.DETAIL_PK,
        "app/modules/users/client_routes.py",
        f"{_VIA_CLIENT_PATH}; SELECT do alvo com AND client_id (anti-IDOR)",
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/users/{user_id}/deactivate",
        ScopeKind.DETAIL_PK,
        "app/modules/users/client_routes.py",
        f"{_VIA_CLIENT_PATH}; SELECT do alvo com AND client_id (anti-IDOR)",
    ),
    # ---------------------------------------------------------------- glossário (S6)
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/glossary",
        ScopeKind.COLLECTION,
        "app/modules/glossary/routes.py",
        _VIA_CLIENT_PATH,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/glossary",
        ScopeKind.COLLECTION,
        "app/modules/glossary/routes.py",
        f"{_VIA_CLIENT_PATH}; client_id da entrada fixado pelo servidor",
    ),
    SensitiveEndpoint(
        "PATCH",
        "/api/v1/clients/{client_id}/glossary/{entry_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/glossary/routes.py",
        f"{_VIA_CLIENT_PATH}; SELECT do alvo com AND client_id (anti-IDOR)",
    ),
    SensitiveEndpoint(
        "DELETE",
        "/api/v1/clients/{client_id}/glossary/{entry_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/glossary/routes.py",
        f"{_VIA_CLIENT_PATH}; SELECT do alvo com AND client_id (anti-IDOR)",
    ),
    # ------------------------------------------- conexões de origem (S9, 09.3)
    # A credencial da origem é o dado mais sensível que estas rotas tocam — e
    # nenhuma delas o devolve. O que se protege aqui é o de sempre: a conexão
    # pertence a UM cliente, e o `client_id` do path é a única porta.
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/connections",
        ScopeKind.COLLECTION,
        "app/modules/client_connections/routes.py",
        _VIA_CLIENT_PATH,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/connections",
        ScopeKind.COLLECTION,
        "app/modules/client_connections/routes.py",
        f"{_VIA_CLIENT_PATH} + OpenClientDep + ManageClientConnectionsDep; "
        "client_id da conexão fixado pelo servidor",
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/connections/{connection_id}/test",
        ScopeKind.DETAIL_PK,
        "app/modules/client_connections/routes.py",
        f"{_VIA_CLIENT_PATH} + OpenClientDep + ManageClientConnectionsDep; "
        "SELECT do alvo com AND client_id (anti-IDOR)",
    ),
    SensitiveEndpoint(
        "PATCH",
        "/api/v1/clients/{client_id}/connections/{connection_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/client_connections/routes.py",
        f"{_VIA_CLIENT_PATH} + OpenClientDep + ManageClientConnectionsDep; "
        "SELECT do alvo com AND client_id (anti-IDOR)",
    ),
    SensitiveEndpoint(
        "DELETE",
        "/api/v1/clients/{client_id}/connections/{connection_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/client_connections/routes.py",
        f"{_VIA_CLIENT_PATH} + OpenClientDep + ManageClientConnectionsDep; "
        "DELETE com AND client_id (anti-IDOR)",
    ),
    # ------------------------------------ plano de contas (S10, 10.3)
    # A classificação contábil do cliente: códigos, hierarquia e o vínculo com
    # a conta de demonstrativo. Nenhum NOME é persistido (§4.5), mas os códigos
    # são dado do tenant — e a lista revela o desenho contábil de quem a tem.
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/chart-of-accounts",
        ScopeKind.COLLECTION,
        "app/modules/client_chart_of_accounts/routes.py",
        f"{_VIA_CLIENT_PATH} + ViewClientChartOfAccountsDep; todo SELECT nasce de "
        "_base_query, que já leva AND client_id",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/chart-of-accounts/coverage",
        ScopeKind.COLLECTION,
        "app/modules/client_chart_of_accounts/routes.py",
        f"{_VIA_CLIENT_PATH} + ViewClientChartOfAccountsDep; a agregação filtra "
        "por client_id na própria query",
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/chart-of-accounts/sync",
        ScopeKind.COLLECTION,
        "app/modules/client_chart_of_accounts/routes.py",
        f"{_VIA_CLIENT_PATH} + OpenClientDep + SyncClientChartOfAccountsDep; "
        "cliente encerrado = 409 e a leitura segue 200",
    ),
    # ------------------------------ carteira de títulos (S11, 11.5)
    # A posição financeira do cliente: o que ele deve, o que tem a receber, de
    # quem e desde quando. Nenhum NOME é persistido (§4.5) — mas vazar esta
    # coleção é vazar quanto um cliente está inadimplente, que é o dado mais
    # sensível que a plataforma passa a guardar nesta sprint.
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/titles",
        ScopeKind.COLLECTION,
        "app/modules/client_titles/routes.py",
        f"{_VIA_CLIENT_PATH} + ViewClientReceivablesDep; todo SELECT nasce de "
        "_base_query, que já leva AND client_id",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/titles/summary",
        ScopeKind.COLLECTION,
        "app/modules/client_titles/routes.py",
        f"{_VIA_CLIENT_PATH} + ViewClientReceivablesDep; a agregação do aging "
        "filtra por client_id na própria query",
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/titles/sync",
        ScopeKind.COLLECTION,
        "app/modules/client_titles/routes.py",
        f"{_VIA_CLIENT_PATH} + OpenClientDep + SyncClientReceivablesDep; "
        "cliente encerrado = 409 e a leitura segue 200",
    ),
    # ------------------------- contexto do título (S15, BACK 15.1)
    # Acordo, antecipação, perda provável — a leitura por trás do relatório de
    # recebíveis (BACK 15.2). `title_id` não vem sozinho: a rota carrega o
    # TÍTULO pela PK **restrita ao `client_id` do path**
    # (`TitleContextRepository.get_title_for_client`), então título de outro
    # cliente é 404, não um vazamento por PK solta.
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/titles/{title_id}/context",
        ScopeKind.COLLECTION,
        "app/modules/client_titles/routes.py",
        f"{_VIA_CLIENT_PATH} + OpenClientDep + ManageTitleContextDep; título "
        "buscado por get_title_for_client (AND client_id = tenant); título de "
        "outro cliente = 404 sem vazar",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/titles/{title_id}/context",
        ScopeKind.COLLECTION,
        "app/modules/client_titles/routes.py",
        f"{_VIA_CLIENT_PATH} + ViewTitleContextDep; título buscado por "
        "get_title_for_client (AND client_id = tenant); título de outro "
        "cliente = 404 sem vazar",
    ),
    # ------------------------- relatório de recebíveis (S15, BACK 15.2)
    # Só agregados (nunca texto decifrado nem identificador de título) — usa a
    # MESMA permissão de leitura da carteira (view_client_receivables), não
    # view_title_context.
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/titles/receivables-report",
        ScopeKind.COLLECTION,
        "app/modules/client_titles/routes.py",
        f"{_VIA_CLIENT_PATH} + ViewClientReceivablesDep; a agregação filtra "
        "por client_id na própria query",
    ),
)

#: Endpoints do denominador que AINDA não têm o mecanismo no código. Ficam na
#: lista porque o denominador é fechado na abertura da sprint (senão a métrica
#: muda de base no meio do caminho), mas o teste de existência os pula e o de
#: cobertura os conta como NÃO cobertos. Esvaziar este conjunto é parte do DoD.
#:
#: Vazio desde a BACK 05.5, salvo uma janela: em 86e36ecnp as 4 rotas de
#: `client-categories` entraram no denominador (o catálogo é por organização
#: desde a migration `3e8f1a6c9d24`) antes do filtro chegar, e 86e36ecqz o
#: esvaziou de novo — a cobertura contou o buraco enquanto ele existiu.
PENDING_ENDPOINTS: dict[str, str] = {}

#: Rotas `/api/v1` que **não** são sensíveis a tenant, com o porquê. Existe para
#: que o teste de completude possa afirmar "toda rota está classificada" — uma
#: rota nova cai fora das duas listas e o teste falha, em vez de passar por
#: omissão.
NON_TENANT_ENDPOINTS: dict[str, str] = {
    "POST /api/v1/auth/login": "autenticação — ainda não há usuário",
    "POST /api/v1/auth/refresh": "autenticação — opera sobre o próprio token",
    "POST /api/v1/auth/logout": "autenticação — apenas limpa cookies",
    "GET /api/v1/anomaly-types": "taxonomia global do produto; sem dado de cliente nem de org",
    "POST /api/v1/anomaly-types": "taxonomia global; escrita só da plataforma (MANAGE_ANOMALY_TYPES)",
    "PATCH /api/v1/anomaly-types/{type_id}": "taxonomia global; escrita só da plataforma",
    "DELETE /api/v1/anomaly-types/{type_id}": "taxonomia global; escrita só da plataforma",
    "POST /api/v1/clients/test-connection": "valida credenciais enviadas no body; nada persistido",
    "POST /api/v1/system/alert-test": "diagnóstico de alerting; plataforma ou admin (RUN_ALERT_TEST)",
    "GET /api/v1/organizations": "administração da plataforma (ManagePlatformDep); sem dado de cliente",
    "POST /api/v1/organizations": "administração da plataforma (ManagePlatformDep)",
    "GET /api/v1/organizations/platform-admins": "administração da plataforma (ManagePlatformDep); só quem tem scope=platform, sem organização nem cliente",
    "GET /api/v1/organizations/{organization_id}": "administração da plataforma (ManagePlatformDep)",
    "PATCH /api/v1/organizations/{organization_id}": "administração da plataforma (ManagePlatformDep)",
}
