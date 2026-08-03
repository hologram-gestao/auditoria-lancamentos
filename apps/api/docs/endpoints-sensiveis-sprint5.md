# Endpoints sensíveis a tenant — Sprint 5 (R3 / BACK 05.4)

> **Artefato versionado da sprint.** É o **denominador fechado** da métrica
> "endpoints sensíveis com caso negativo cross-tenant testado e passando ÷ total".
> Sem esta lista, "100%" seria um número sobre um conjunto arbitrário.
>
> **Fonte única:** `apps/api/app/core/sensitive_endpoints.py`. Esta página é
> gerada por `scripts/gen_sensitive_endpoints_doc.py` — não edite aqui, edite
> lá. O `tests/integration/test_sensitive_endpoints.py` falha se a lista
> divergir das rotas reais **ou** se uma rota nova com
> `{client_id}`/`{session_id}` não for classificada.


## Placar

| | |
| --- | --- |
| Endpoints sensíveis (denominador) | **38** |
| Com caso negativo cross-tenant verde | **38** |
| Pendentes (implementação em outra task) | **0** |
| Cobertura | **38/38 = 100%** |

## Lista canônica

Legenda de `tipo`: **coleção** = vaza forjando `client_id` na URL/payload · **detalhe (PK)** = vaza pela PK do recurso, **sem** `client_id` na requisição (o mais fácil de esquecer).

| Método | Path | Tipo | Módulo | Como o tenant é imposto | Status |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/clients/{client_id}/reconciliations` | coleção | `app/modules/clients/routes.py` | AccessibleClientDep -> require_client_access -> resolve_client_access (o client_id do path só passa se for o tenant da linha) | ✅ verde |
| `POST` | `/api/v1/reconciliations` | coleção | `app/modules/reconciliations/routes.py` | client_id do body validado por require_client_access antes de criar | ✅ verde |
| `GET` | `/api/v1/reconciliations/check-duplicate` | coleção | `app/modules/reconciliations/routes.py` | client_id da query validado por require_client_access | ✅ verde |
| `POST` | `/api/v1/reconciliations/parse` | coleção | `app/modules/reconciliations/routes.py` | client_id do body validado por require_client_access | ✅ verde |
| `GET` | `/api/v1/reconciliations/{session_id}` | detalhe (PK) | `app/modules/reconciliations/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme | ✅ verde |
| `GET` | `/api/v1/reconciliations/{session_id}/status` | detalhe (PK) | `app/modules/reconciliations/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme | ✅ verde |
| `POST` | `/api/v1/reconciliations/{session_id}/reprocess` | detalhe (PK) | `app/modules/reconciliations/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme | ✅ verde |
| `POST` | `/api/v1/reconciliations/{session_id}/cancel` | detalhe (PK) | `app/modules/reconciliations/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme | ✅ verde |
| `POST` | `/api/v1/reconciliations/{session_id}/discard` | detalhe (PK) | `app/modules/reconciliations/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme | ✅ verde |
| `GET` | `/api/v1/reconciliations/{session_id}/anomalies` | coleção | `app/modules/reconciliations/review/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme | ✅ verde |
| `POST` | `/api/v1/reconciliations/{session_id}/anomalies` | coleção | `app/modules/reconciliations/review/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme | ✅ verde |
| `PATCH` | `/api/v1/reconciliations/{session_id}/anomalies/{anomaly_id}` | detalhe (PK) | `app/modules/reconciliations/review/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme; e get_anomaly filtra AND session_id | ✅ verde |
| `GET` | `/api/v1/reconciliations/{session_id}/files` | coleção | `app/modules/reconciliations/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme | ✅ verde |
| `POST` | `/api/v1/reconciliations/{session_id}/files` | coleção | `app/modules/reconciliations/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme | ✅ verde |
| `DELETE` | `/api/v1/reconciliations/{session_id}/files/{file_id}` | detalhe (PK) | `app/modules/reconciliations/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme; e get_file filtra AND session_id | ✅ verde |
| `GET` | `/api/v1/reconciliations/{session_id}/file-entries` | coleção | `app/modules/reconciliations/review/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme | ✅ verde |
| `PATCH` | `/api/v1/reconciliations/{session_id}/file-entries/{entry_id}` | detalhe (PK) | `app/modules/reconciliations/review/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme; e get_file_entry filtra AND session_id | ✅ verde |
| `GET` | `/api/v1/reconciliations/{session_id}/omie-entries` | coleção | `app/modules/reconciliations/review/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme | ✅ verde |
| `PATCH` | `/api/v1/reconciliations/{session_id}/omie-entries/{entry_id}` | detalhe (PK) | `app/modules/reconciliations/review/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme; e get_omie_entry filtra AND session_id | ✅ verde |
| `GET` | `/api/v1/reconciliations/{session_id}/available-omie-entries` | coleção | `app/modules/reconciliations/review/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme | ✅ verde |
| `GET` | `/api/v1/clients/{client_id}` | detalhe (PK) | `app/modules/clients/routes.py` | AccessibleClientDep -> require_client_access -> resolve_client_access (o client_id do path só passa se for o tenant da linha) | ✅ verde |
| `PATCH` | `/api/v1/clients/{client_id}/sync-accounts` | detalhe (PK) | `app/modules/clients/routes.py` | AccessibleClientDep -> require_client_access -> resolve_client_access (o client_id do path só passa se for o tenant da linha) | ✅ verde |
| `GET` | `/api/v1/omie/lancamentos` | coleção | `app/modules/omie_data/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme | ✅ verde |
| `GET` | `/api/v1/notifications` | coleção | `app/modules/notifications/repository.py` | _visibility_filter: user_id = eu AND client_id = <tenant da linha> | ✅ verde |
| `GET` | `/api/v1/notifications/unread-count` | coleção | `app/modules/notifications/repository.py` | _visibility_filter: user_id = eu AND client_id = <tenant da linha> | ✅ verde |
| `POST` | `/api/v1/notifications/{notification_id}/read` | detalhe (PK) | `app/modules/notifications/repository.py` | get_for_user com o mesmo _visibility_filter; 404 uniforme | ✅ verde |
| `POST` | `/api/v1/reconciliations/{session_id}/export` | detalhe (PK) | `app/modules/reconciliations/export/routes.py` | require_session_access: SELECT da sessão já com AND client_id = <tenant da linha> (scoped_by_tenant) + resolve_client_access; 404 uniforme | ✅ verde |
| `POST` | `/api/v1/usage-events` | detalhe (PK) | `app/modules/usage_events/repository.py` | get_session_client_id com scoped_by_tenant; sessão alheia vira 404 | ✅ verde |
| `GET` | `/api/v1/clients/{client_id}/users` | coleção | `app/modules/users/client_routes.py` | AccessibleClientDep -> require_client_access -> resolve_client_access (o client_id do path só passa se for o tenant da linha) | ✅ verde |
| `POST` | `/api/v1/clients/{client_id}/users` | coleção | `app/modules/users/client_routes.py` | AccessibleClientDep -> require_client_access -> resolve_client_access (o client_id do path só passa se for o tenant da linha); client_id do novo usuário fixado pelo servidor | ✅ verde |
| `GET` | `/api/v1/clients/{client_id}/users/{user_id}` | detalhe (PK) | `app/modules/users/client_routes.py` | AccessibleClientDep -> require_client_access -> resolve_client_access (o client_id do path só passa se for o tenant da linha); SELECT do alvo com AND client_id (anti-IDOR) | ✅ verde |
| `PATCH` | `/api/v1/clients/{client_id}/users/{user_id}` | detalhe (PK) | `app/modules/users/client_routes.py` | AccessibleClientDep -> require_client_access -> resolve_client_access (o client_id do path só passa se for o tenant da linha); SELECT do alvo com AND client_id (anti-IDOR) | ✅ verde |
| `POST` | `/api/v1/clients/{client_id}/users/{user_id}/activate` | detalhe (PK) | `app/modules/users/client_routes.py` | AccessibleClientDep -> require_client_access -> resolve_client_access (o client_id do path só passa se for o tenant da linha); SELECT do alvo com AND client_id (anti-IDOR) | ✅ verde |
| `POST` | `/api/v1/clients/{client_id}/users/{user_id}/deactivate` | detalhe (PK) | `app/modules/users/client_routes.py` | AccessibleClientDep -> require_client_access -> resolve_client_access (o client_id do path só passa se for o tenant da linha); SELECT do alvo com AND client_id (anti-IDOR) | ✅ verde |
| `GET` | `/api/v1/clients/{client_id}/glossary` | coleção | `app/modules/glossary/routes.py` | AccessibleClientDep -> require_client_access -> resolve_client_access (o client_id do path só passa se for o tenant da linha) | ✅ verde |
| `POST` | `/api/v1/clients/{client_id}/glossary` | coleção | `app/modules/glossary/routes.py` | AccessibleClientDep -> require_client_access -> resolve_client_access (o client_id do path só passa se for o tenant da linha); client_id da entrada fixado pelo servidor | ✅ verde |
| `PATCH` | `/api/v1/clients/{client_id}/glossary/{entry_id}` | detalhe (PK) | `app/modules/glossary/routes.py` | AccessibleClientDep -> require_client_access -> resolve_client_access (o client_id do path só passa se for o tenant da linha); SELECT do alvo com AND client_id (anti-IDOR) | ✅ verde |
| `DELETE` | `/api/v1/clients/{client_id}/glossary/{entry_id}` | detalhe (PK) | `app/modules/glossary/routes.py` | AccessibleClientDep -> require_client_access -> resolve_client_access (o client_id do path só passa se for o tenant da linha); SELECT do alvo com AND client_id (anti-IDOR) | ✅ verde |

## Rotas `/api/v1` fora do denominador

Não carregam dado escopável a um cliente. Registradas explicitamente para que o teste de completude possa afirmar "toda rota está classificada" — rota nova cai fora das duas listas e o CI falha, em vez de passar por omissão.

| Rota | Por que não é sensível a tenant |
| --- | --- |
| `DELETE /api/v1/anomaly-types/{type_id}` | configuração global; admin-only |
| `GET /api/v1/anomaly-types` | configuração global; sem dado de cliente |
| `GET /api/v1/clients` | listagem da equipe Hologram; staff-only (papel de cliente = 403) |
| `GET /api/v1/users` | usuários do SISTEMA; admin-only |
| `GET /api/v1/users/{user_id}` | usuários do SISTEMA; admin-only |
| `PATCH /api/v1/anomaly-types/{type_id}` | configuração global; admin-only |
| `PATCH /api/v1/clients/{client_id}` | edita o cliente; admin-only pela matriz (EDIT_CLIENT) |
| `PATCH /api/v1/clients/{client_id}/assign` | reatribui carteira; admin-only |
| `PATCH /api/v1/users/{user_id}` | usuários do SISTEMA; admin-only |
| `POST /api/v1/anomaly-types` | configuração global; admin-only |
| `POST /api/v1/auth/login` | autenticação — ainda não há usuário |
| `POST /api/v1/auth/logout` | autenticação — apenas limpa cookies |
| `POST /api/v1/auth/refresh` | autenticação — opera sobre o próprio token |
| `POST /api/v1/clients` | cria cliente; staff-only |
| `POST /api/v1/clients/test-connection` | valida credenciais enviadas no body; nada persistido |
| `POST /api/v1/system/alert-test` | diagnóstico de alerting; admin-only |
| `POST /api/v1/users` | usuários do SISTEMA; admin-only |
| `POST /api/v1/users/{user_id}/activate` | usuários do SISTEMA; admin-only |
| `POST /api/v1/users/{user_id}/deactivate` | usuários do SISTEMA; admin-only |

## Como a cobertura é medida

`tests/integration/test_sensitive_endpoints.py` parametriza **toda** a lista e,
para cada endpoint, faz um operador do tenant A disparar a rota contra recursos
do tenant B. Critérios de cada caso:

- nunca `2xx` (para detalhe/PK e coleções endereçadas por `client_id`);
- coleções globais (notificações) respondem `200` com **zero** linhas de B;
- o corpo **nunca** contém dado de B (o teste procura a razão social do alvo);
- o body enviado é **válido** de propósito — um `422` de validação passaria sem
  nunca chegar na autorização, e a cobertura seria falsa.

