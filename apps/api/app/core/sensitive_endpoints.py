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
_VIA_MAPPING_CATALOG_ORG = (
    "scoped_by_organization no SELECT do destino (AND organization_id = <org da LINHA "
    "do observador>; plataforma: todas); destino/alvo de outra organização = 404"
)
_VIA_CLIENT_MAPPING_WRITE = (
    "AccessibleClientDep -> resolve_client_access + OpenClientDep + "
    "ManageClientMappingDep (guard auditado); destino resolvido na org DO CLIENTE; "
    "toda query de decisão filtra client_id"
)
_VIA_EXPORT_LAYOUT_ORG = (
    "ReadExportLayoutsDep/ManageExportLayoutsDep (guard de organização: usuário de cliente "
    "= 403 com linha denied) + scoped_by_organization no SELECT do layout (AND "
    "organization_id = <org da LINHA do observador>; plataforma: todas); layout de outra "
    "organização = 404; o layout novo nasce na org da LINHA do ator "
    "(resolve_organization_for_creation)"
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
        "POST",
        "/api/v1/users/{user_id}/password",
        ScopeKind.DETAIL_PK,
        "app/modules/users/routes.py",
        "ResetUserPasswordDep (só plataforma; admin e gerente de QUALQUER organização, "
        "inclusive a do alvo, e usuário de cliente = 403 antes de tocar a linha). O alvo é "
        "por PK em `users` inteira; a própria senha = 409; tenant encerrado = 409 "
        "(86e3ewukz)",
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/users/{user_id}/sessions/revoke",
        ScopeKind.DETAIL_PK,
        "app/modules/users/routes.py",
        "ManageOrgUsersDep + get_session_revocation_target/scoped_by_organization: AND "
        "scope IN ('system' [, 'platform' só para a plataforma]) AND organization_id = <org do "
        "observador> no próprio SELECT (plataforma: todas); usuário de cliente, staff de outra "
        "org e, para o admin, linha de plataforma = 404; a própria sessão = 409; grava só "
        "`password_changed_at` (hash e `active` intocados) — revogação sem troca de senha "
        "(86e3anx4u)",
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
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/users/{user_id}/sessions/revoke",
        ScopeKind.DETAIL_PK,
        "app/modules/users/client_routes.py",
        f"{_VIA_CLIENT_PATH} (ManageClientUsersAuditedDep: o operador negado grava `denied`); "
        "SELECT do alvo com AND client_id (anti-IDOR); a própria sessão = 409; tenant "
        "encerrado = 409 (OpenClientDep) — revogação sem troca de senha (86e3anx4u)",
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
        "/api/v1/clients/{client_id}/summary",
        ScopeKind.COLLECTION,
        "app/modules/client_summary/routes.py",
        f"{_VIA_CLIENT_PATH}; cada contagem com client_id no próprio SELECT + "
        "scoped_by_tenant; o bloco da carteira só com view_client_receivables (sem ela, null)",
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
    # ------------------------- base de movimentos (S12, BACK 12.2 — R0)
    # Nenhum NOME é persistido, mas vazar esta coleção é vazar o extrato do
    # cliente (valores, datas, códigos de categoria e de fornecedor) — é a
    # entrada do de-para.
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/movements/sync",
        ScopeKind.COLLECTION,
        "app/modules/client_movements/routes.py",
        f"{_VIA_CLIENT_PATH} + OpenClientDep + SyncClientMovementsDep (guard "
        "auditado); todo SELECT/UPDATE da base filtra client_id; cliente "
        "encerrado = 409",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/movements/sync-state",
        ScopeKind.COLLECTION,
        "app/modules/client_movements/routes.py",
        f"{_VIA_CLIENT_PATH}; o estado é lido por (client_id, competência) na própria query",
    ),
    # ------------------------- catálogo do de-para (S12, BACK 12.3 — R1)
    # Por ORGANIZAÇÃO: o plano de demonstração de um BPO. Vazar é expor como o
    # escritório estrutura os demonstrativos dos clientes dele.
    SensitiveEndpoint(
        "GET",
        "/api/v1/mapping-destinations",
        ScopeKind.COLLECTION,
        "app/modules/mapping_catalog/routes.py",
        _VIA_MAPPING_CATALOG_ORG,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/mapping-destinations",
        ScopeKind.COLLECTION,
        "app/modules/mapping_catalog/routes.py",
        "ManageMappingCatalogDep; o destino nasce na org da LINHA do ator "
        "(resolve_organization_for_creation); org suspensa = 409",
    ),
    SensitiveEndpoint(
        "PATCH",
        "/api/v1/mapping-destinations/{destination_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/mapping_catalog/routes.py",
        "ManageMappingCatalogDep + " + _VIA_MAPPING_CATALOG_ORG,
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/mapping-destinations/{destination_id}/targets",
        ScopeKind.DETAIL_PK,
        "app/modules/mapping_catalog/routes.py",
        _VIA_MAPPING_CATALOG_ORG,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/mapping-destinations/{destination_id}/targets",
        ScopeKind.DETAIL_PK,
        "app/modules/mapping_catalog/routes.py",
        "ManageMappingCatalogDep + " + _VIA_MAPPING_CATALOG_ORG,
    ),
    SensitiveEndpoint(
        "PATCH",
        "/api/v1/mapping-destinations/{destination_id}/targets/{target_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/mapping_catalog/routes.py",
        "ManageMappingCatalogDep + " + _VIA_MAPPING_CATALOG_ORG + "; alvo buscado "
        "dentro do destino (AND destination_id)",
    ),
    SensitiveEndpoint(
        "DELETE",
        "/api/v1/mapping-destinations/{destination_id}/targets/{target_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/mapping_catalog/routes.py",
        "ManageMappingCatalogDep + " + _VIA_MAPPING_CATALOG_ORG + "; alvo em uso = 409",
    ),
    # ------------------------- decisões do de-para (S12, BACK 12.4 — R2/R4/R7)
    # O desenho contábil do cliente: qual categoria vai para qual conta. O destino
    # é resolvido na organização DO CLIENTE (nunca do payload).
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/mapping/{destination_type}/decisions",
        ScopeKind.COLLECTION,
        "app/modules/client_mapping/routes.py",
        _VIA_CLIENT_MAPPING_WRITE,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/mapping/{destination_type}/decisions/batch",
        ScopeKind.COLLECTION,
        "app/modules/client_mapping/routes.py",
        _VIA_CLIENT_MAPPING_WRITE,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/mapping/{destination_type}/decisions/confirm-inherited",
        ScopeKind.COLLECTION,
        "app/modules/client_mapping/routes.py",
        _VIA_CLIENT_MAPPING_WRITE,
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/mapping/{destination_type}/decisions/history",
        ScopeKind.COLLECTION,
        "app/modules/client_mapping/routes.py",
        f"{_VIA_CLIENT_PATH}; decisões lidas por client_id + destino da org do cliente",
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/mapping/{destination_type}/inherit",
        ScopeKind.COLLECTION,
        "app/modules/client_mapping/routes.py",
        _VIA_CLIENT_MAPPING_WRITE,
    ),
    # ------------------------- leitura e portabilidade do de-para (S12, BACK 12.5)
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/mapping/{destination_type}",
        ScopeKind.COLLECTION,
        "app/modules/client_mapping/routes.py",
        f"{_VIA_CLIENT_PATH}; universo e decisões lidos por client_id",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/mapping/{destination_type}/export",
        ScopeKind.COLLECTION,
        "app/modules/client_mapping/routes.py",
        f"{_VIA_CLIENT_PATH}; 1 linha `export` em access_audit",
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/mapping/{destination_type}/import/preview",
        ScopeKind.COLLECTION,
        "app/modules/client_mapping/routes.py",
        _VIA_CLIENT_MAPPING_WRITE + "; não grava nada",
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/mapping/{destination_type}/import",
        ScopeKind.COLLECTION,
        "app/modules/client_mapping/routes.py",
        _VIA_CLIENT_MAPPING_WRITE,
    ),
    # ------------------------- aplicação e materialização (S12, BACK 12.6)
    # A prévia expõe VALORES da competência (Σ por situação) — o extrato do cliente
    # agregado. A materialização grava o resultado que a Sprint 13 transforma em
    # arquivo contábil.
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/mapping/{destination_type}/preview",
        ScopeKind.COLLECTION,
        "app/modules/client_mapping/routes.py",
        f"{_VIA_CLIENT_PATH}; base e decisões lidas por client_id",
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/mapping/{destination_type}/materializations",
        ScopeKind.COLLECTION,
        "app/modules/client_mapping/routes.py",
        _VIA_CLIENT_MAPPING_WRITE + "; versão N+1 imutável, nunca UPDATE/DELETE",
    ),
    # Follow-up 86e3f0ux7 (item 1): a lista de versões que a tela derivava de
    # `latestVersion`. Leitura por client_id; autor mascarado por escopo (§3.15).
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/mapping/{destination_type}/materializations",
        ScopeKind.COLLECTION,
        "app/modules/client_mapping/routes.py",
        f"{_VIA_CLIENT_PATH}; versões lidas por client_id, autor por author_for_viewer",
    ),
    # ------------------------- mapeamento de entrada do arquivo (S14, BACK 14.1 — R1/R5)
    # Nome de coluna é estrutura, não PII — mas o mapeamento descreve COMO o
    # cliente sem ERP organiza a contabilidade dele, e a escrita muda como todos
    # os próximos arquivos serão lidos.
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/input-mapping",
        ScopeKind.COLLECTION,
        "app/modules/client_input_mappings/routes.py",
        f"{_VIA_CLIENT_PATH}; o mapeamento é lido por client_id na própria query "
        "(ausente = 200 com mapping null, nunca 404)",
    ),
    SensitiveEndpoint(
        "PUT",
        "/api/v1/clients/{client_id}/input-mapping",
        ScopeKind.COLLECTION,
        "app/modules/client_input_mappings/routes.py",
        f"{_VIA_CLIENT_PATH} + OpenClientDep + ManageInputMappingDep (guard auditado); "
        "upsert ON CONFLICT (client_id) com o client_id do path validado; cliente "
        "encerrado = 409",
    ),
    # ------------------------- origem por arquivo (S14, BACK 14.3 — R1/R2/R3)
    # O arquivo do cliente é o EXTRATO dele (valores, datas, descrições, categorias):
    # a inspeção devolve uma amostra das células, o processamento grava a base de
    # movimentos e a lista expõe quem enviou o quê.
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/file-origin/inspect",
        ScopeKind.COLLECTION,
        "app/modules/client_file_ingestion/routes.py",
        f"{_VIA_CLIENT_PATH} + UploadClientFileDep (guard auditado); conexão arquivo "
        "resolvida pelas conexões DO cliente; nada persistido",
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/file-origin/process",
        ScopeKind.COLLECTION,
        "app/modules/client_file_ingestion/routes.py",
        f"{_VIA_CLIENT_PATH} + OpenClientDep + UploadClientFileDep (guard auditado); "
        "mapeamento, categorias, registro e movimentos gravados com o client_id do path "
        "validado; cliente encerrado = 409",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/file-origin/imports",
        ScopeKind.COLLECTION,
        "app/modules/client_file_ingestion/routes.py",
        f"{_VIA_CLIENT_PATH}; registros lidos por client_id, autor por author_for_viewer",
    ),
    # ------------------- plano de contas CONTÁBIL do cliente (S16, BACK 16.1 — R1)
    # As contas do sistema contábil de destino DO cliente: o nome (cifrado) carrega
    # inquilino, pessoa física e fornecedor.
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/accounting-chart",
        ScopeKind.COLLECTION,
        "app/modules/client_accounting_chart/routes.py",
        f"{_VIA_CLIENT_PATH}; toda query do plano filtra client_id no próprio SELECT; "
        "nome decifrado com a DEK do cliente do path",
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/accounting-chart/import",
        ScopeKind.COLLECTION,
        "app/modules/client_accounting_chart/routes.py",
        f"{_VIA_CLIENT_PATH} + OpenClientDep + ManageClientAccountingChartDep (guard "
        "auditado); contas gravadas e inativadas com o client_id do path validado, sob "
        "trava por cliente; cliente encerrado = 409",
    ),
    # ------------- arquivo contábil do cliente (S13, BACK 13.4 — R3)
    # Gerar, listar e baixar: o arquivo leva o histórico do cliente final.
    SensitiveEndpoint(
        "POST",
        "/api/v1/clients/{client_id}/accounting-files",
        ScopeKind.COLLECTION,
        "app/modules/accounting_files/routes.py",
        f"{_VIA_CLIENT_PATH} + GenerateAccountingFileDep (guard auditado) + OpenClientDep; "
        "layout só da organização DO CLIENTE (outra org = 404); materialização carregada com "
        "client_id no WHERE; geração gravada com o client_id do path validado",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/accounting-files",
        ScopeKind.COLLECTION,
        "app/modules/accounting_files/routes.py",
        f"{_VIA_CLIENT_PATH} + GenerateAccountingFileDep (guard auditado); gerações lidas "
        "por client_id no próprio SELECT; autor por author_for_viewer",
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/accounting-files/{generation_id}/download",
        ScopeKind.DETAIL_PK,
        "app/modules/accounting_files/routes.py",
        f"{_VIA_CLIENT_PATH} + GenerateAccountingFileDep (guard auditado) + OpenClientDep; "
        "geração por PK com AND client_id no SELECT (outro cliente = 404); regenera e "
        "confere o SHA-256",
    ),
    # ------------- layouts de exportação do arquivo contábil (S13, BACK 13.2 — R1)
    # Configuração da ORGANIZAÇÃO: o alvo atacado na bateria é um layout de uma TERCEIRA
    # organização (como o catálogo do de-para da S12).
    SensitiveEndpoint(
        "GET",
        "/api/v1/export-layouts",
        ScopeKind.COLLECTION,
        "app/modules/export_layouts/routes.py",
        _VIA_EXPORT_LAYOUT_ORG,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/export-layouts",
        ScopeKind.COLLECTION,
        "app/modules/export_layouts/routes.py",
        _VIA_EXPORT_LAYOUT_ORG,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/export-layouts/from-template",
        ScopeKind.COLLECTION,
        "app/modules/export_layouts/routes.py",
        _VIA_EXPORT_LAYOUT_ORG,
    ),
    SensitiveEndpoint(
        "GET",
        "/api/v1/export-layouts/{layout_id}",
        ScopeKind.DETAIL_PK,
        "app/modules/export_layouts/routes.py",
        _VIA_EXPORT_LAYOUT_ORG,
    ),
    SensitiveEndpoint(
        "POST",
        "/api/v1/export-layouts/{layout_id}/versions",
        ScopeKind.DETAIL_PK,
        "app/modules/export_layouts/routes.py",
        _VIA_EXPORT_LAYOUT_ORG + "; SELECT ... FOR UPDATE já restrito ao alcance",
    ),
    # ------------- conta contábil do BANCO de cada conta de origem (S16, BACK 16.3 — R3)
    SensitiveEndpoint(
        "GET",
        "/api/v1/clients/{client_id}/source-accounts",
        ScopeKind.COLLECTION,
        "app/modules/client_source_accounts/routes.py",
        f"{_VIA_CLIENT_PATH}; associações, base de movimentos e conexões lidas por "
        "client_id; o JOIN com o plano carrega client_id dos dois lados",
    ),
    SensitiveEndpoint(
        "PUT",
        "/api/v1/clients/{client_id}/source-accounts",
        ScopeKind.COLLECTION,
        "app/modules/client_source_accounts/routes.py",
        f"{_VIA_CLIENT_PATH} + OpenClientDep + ManageClientAccountingChartDep (guard "
        "auditado); a conta do banco passa pelo validador único (SELECT com client_id do "
        "path: outro cliente = 404); upsert ON CONFLICT com o client_id validado",
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
    "GET /api/v1/export-layout-templates": "modelos de layout declarados no CÓDIGO (dado de código, igual para todas as organizações); sem dado de cliente nem de org",
    "POST /api/v1/system/alert-test": "diagnóstico de alerting; plataforma ou admin (RUN_ALERT_TEST)",
    "GET /api/v1/organizations": "administração da plataforma (ManagePlatformDep); sem dado de cliente",
    "POST /api/v1/organizations": "administração da plataforma (ManagePlatformDep)",
    "GET /api/v1/organizations/platform-admins": "administração da plataforma (ManagePlatformDep); só quem tem scope=platform, sem organização nem cliente",
    "GET /api/v1/organizations/{organization_id}": "administração da plataforma (ManagePlatformDep)",
    "PATCH /api/v1/organizations/{organization_id}": "administração da plataforma (ManagePlatformDep)",
    "POST /api/v1/leads": "público; captação de lead da landing; não lê nem grava dado escopável",
}
