"""Lista CANÔNICA de endpoints sensíveis a tenant (Sprint 5 / R3 — BACK 05.4).

É o **denominador fechado** da métrica da sprint ("endpoints sensíveis com caso
negativo cross-tenant testado e passando ÷ total"). Sem esta lista, "100%" é um
número sobre um conjunto arbitrário e inverificável.

Endpoint sensível = rota que **lê ou muta dado escopável a um cliente/tenant**,
tanto por coleção quanto por PK do recurso. Rotas de autenticação, de
configuração global (tipos de anomalia) e de administração do sistema (usuários
da equipe Hologram, alert-test) **não** entram: não carregam dado de cliente.

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
_VIA_SESSION = (
    "require_session_access: SELECT da sessão já com AND client_id = <tenant da "
    "linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme"
)
_VIA_CLIENT_PATH = (
    "AccessibleClientDep -> require_client_access -> resolve_client_access "
    "(o client_id do path só passa se for o tenant da linha)"
)

SENSITIVE_ENDPOINTS: tuple[SensitiveEndpoint, ...] = (
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
    SensitiveEndpoint(
        "GET",
        "/api/v1/omie/lancamentos",
        ScopeKind.COLLECTION,
        "app/modules/omie_data/routes.py",
        _VIA_SESSION,
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
)

#: Endpoints do denominador que AINDA não existem no código. Ficam na lista
#: porque o denominador é fechado na abertura da sprint (senão a métrica muda de
#: base no meio do caminho), mas o teste de existência os pula e o de cobertura
#: os conta como NÃO cobertos. Esvaziar este conjunto é parte do DoD.
#:
#: **Vazio desde a BACK 05.5** — os 6 endpoints de usuários do cliente saíram
#: daqui quando foram implementados; a cobertura fechou em 34/34. A Sprint 6
#: acrescentou as 4 rotas do glossário JÁ implementadas (38/38), então este
#: conjunto continua vazio.
PENDING_ENDPOINTS: dict[str, str] = {}

#: Rotas `/api/v1` que **não** são sensíveis a tenant, com o porquê. Existe para
#: que o teste de completude possa afirmar "toda rota está classificada" — uma
#: rota nova cai fora das duas listas e o teste falha, em vez de passar por
#: omissão.
NON_TENANT_ENDPOINTS: dict[str, str] = {
    "POST /api/v1/auth/login": "autenticação — ainda não há usuário",
    "POST /api/v1/auth/refresh": "autenticação — opera sobre o próprio token",
    "POST /api/v1/auth/logout": "autenticação — apenas limpa cookies",
    "GET /api/v1/anomaly-types": "configuração global; sem dado de cliente",
    "POST /api/v1/anomaly-types": "configuração global; admin-only",
    "PATCH /api/v1/anomaly-types/{type_id}": "configuração global; admin-only",
    "DELETE /api/v1/anomaly-types/{type_id}": "configuração global; admin-only",
    "GET /api/v1/clients": "listagem da equipe Hologram; staff-only (papel de cliente = 403)",
    "POST /api/v1/clients": "cria cliente; staff-only",
    "POST /api/v1/clients/test-connection": "valida credenciais enviadas no body; nada persistido",
    "PATCH /api/v1/clients/{client_id}": "edita o cliente; admin-only pela matriz (EDIT_CLIENT)",
    "PATCH /api/v1/clients/{client_id}/assign": "reatribui carteira; admin-only",
    "GET /api/v1/users": "usuários do SISTEMA; admin-only",
    "POST /api/v1/users": "usuários do SISTEMA; admin-only",
    "GET /api/v1/users/{user_id}": "usuários do SISTEMA; admin-only",
    "PATCH /api/v1/users/{user_id}": "usuários do SISTEMA; admin-only",
    "POST /api/v1/users/{user_id}/activate": "usuários do SISTEMA; admin-only",
    "POST /api/v1/users/{user_id}/deactivate": "usuários do SISTEMA; admin-only",
    "POST /api/v1/system/alert-test": "diagnóstico de alerting; admin-only",
}
