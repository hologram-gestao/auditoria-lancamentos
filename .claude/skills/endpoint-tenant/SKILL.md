---
name: endpoint-tenant
description: >
  Roteiro OBRIGATÓRIO ao criar ou alterar rota em apps/api/app/modules/ que toque
  dado de cliente, e para qualquer query nova que leia dado escopável. Gatilhos
  literais: "endpoint novo", "nova rota", "listar X do cliente", adicionar rota em
  routes.py, query nova em repository. Endpoint sem filtro de tenant é VAZAMENTO
  entre clientes — a regra de maior custo de erro do sistema (CLAUDE.md §3.15).
---

# /endpoint-tenant — endpoint novo que toca dado escopável

A CLAUDE.md §3.15 se descreve como "a regra mais fácil de furar sem perceber".
Esta skill a transforma em 5 passos, cada um com o comando que o prova. Os números
de linha abaixo foram conferidos em 09/09/2026 — se um grep não bater, o código
mudou: releia o arquivo, não confie na linha.

## Passo 1 — UMA decisão de acesso (nunca uma segunda implementação)

A autorização passa por `resolve_client_access` (`app/core/authz.py:149`). Na rota,
isso significa usar as dependências prontas — nunca comparar `role`/`client_id` na
mão:

- **Tenant**: `AccessibleClientDep` (`app/core/dependencies.py:177`) — valida o
  `client_id` do path contra a decisão única e devolve o `Client` carregado.
- **Permissão**: os guards da matriz (`RunReconciliationDep`, `ManageGlossaryDep`,
  `EditClientDep`…), que consultam `has_permission` (`authz.py:126`) contra a
  `PERMISSION_MATRIX` (`authz.py:86`). Ação nova = permissão nova na matriz, não
  `if` na rota.

Prova (na sua rota nova e no módulo inteiro):

```bash
grep -n "AccessibleClientDep\|require_client_access" app/modules/<modulo>/routes.py
grep -rn "role ==" app/modules/<modulo>/   # esperado: NADA novo
```

## Passo 2 — o tenant vem da LINHA do usuário

Nunca de `client_id` recebido em URL, query ou body. O JWT até carrega
`scope`/`client_id`, mas a autoridade é a linha lida por `get_current_user` a cada
request — é isso que faz revogação valer no request SEGUINTE, sem esperar token
expirar. No service/repository, o tenant a forçar vem de
`tenant_filter_client_id(user)` (`authz.py:135`) ou do `Client` já validado pelo
`AccessibleClientDep` — jamais do payload.

```bash
grep -n "tenant_filter_client_id\|user.client_id" app/modules/<modulo>/
```

## Passo 3 — defense-in-depth NA QUERY (negar na rota não basta)

Um endpoint futuro que esqueça o guard não pode virar vazamento:

- **Coleção** → `scoped_by_tenant(stmt, <coluna client_id>, user)`
  (`authz.py:189`) dentro do próprio SELECT.
- **Detalhe por PK** → `AND client_id = <tenant>` no SELECT (padrão
  `get_by_id_in_tenant`, ver `app/modules/users/repository.py`). Recurso de outro
  tenant simplesmente não carrega → **404**, nunca o dado.

```bash
grep -n "scoped_by_tenant\|client_id ==" app/modules/<modulo>/repository.py
```

## Passo 4 — registrar na lista canônica (o denominador da métrica)

Toda rota sensível entra em `app/core/sensitive_endpoints.py`
(`SENSITIVE_ENDPOINTS`, linha 65) com o `ScopeKind` certo (`:27`):

- `COLLECTION` (`:31`) — vaza se alguém forjar `client_id` no filtro;
- `DETAIL_PK` (`:35`) — o MAIS fácil de esquecer: não há nada no request para
  filtrar, o SELECT é que tem de carregar o tenant.

Endpoint fora da lista é buraco que ninguém mede. O guardião
`test_toda_rota_da_api_esta_classificada`
(`tests/integration/test_sensitive_endpoints.py:84`) **quebra o CI** se uma rota
nova não for classificada — em `SENSITIVE_ENDPOINTS` ou, com justificativa, em
`NON_TENANT_ENDPOINTS` (`:406`). Depois, regenere a doc:

```bash
uv run python scripts/gen_sensitive_endpoints_doc.py   # atualiza docs/endpoints-sensiveis-sprint5.md
```

**Critério de "sensível":** lê ou escreve dado de UM cliente (via `{client_id}` no
path, via sessão de conciliação, ou coleção filtrável por tenant) → entra. O que
NÃO entra (vai para `NON_TENANT_ENDPOINTS`, sempre com justificativa): auth/login,
health/system, catálogos globais e administração de usuários do sistema. Na
dúvida, é sensível.

## Passo 5 — teste negativo cross-tenant (sem ele, o passo 4 é só cadastro)

Registrar na lista já te dá o caso parametrizado
`test_cross_tenant_por_endpoint` (`test_sensitive_endpoints.py:312`), que roda 1
negativo por endpoint coberto (`COVERED`, `:308` — e `PENDING_ENDPOINTS` vazio é o
estado saudável). Se a rota tem semântica própria (filtro extra, máscara, side
effect), acrescente um caso dedicado em
`tests/integration/test_tenant_isolation.py` (`TestCrossTenant`, `:220`).

```bash
uv run --extra dev pytest tests/integration/test_sensitive_endpoints.py -q --no-cov
```

## Regras auxiliares que andam juntas

- **Negação não vaza o alvo**: 403 (ou 404 onde a conversão anti-enumeração já
  existe) com corpo SEM nome, razão social ou CNPJ do tenant alvo, e **1** linha em
  `access_audit` via `record_cross_tenant_denied` (`app/core/audit.py:90`).
- **Navegação no próprio tenant NÃO audita** — `record_access` (`audit.py:45`) só
  para `denied|view|export`; a trilha não infla com uso normal.
- **Expôs QUEM fez algo?** Só `{name, email}`, mascarado por escopo via
  `author_for_viewer` (`app/modules/reconciliations/service.py`) — nunca a linha
  de `users` (§3.15).
- **Front**: o espelho é `apps/web/src/lib/authz.ts` — UM helper, nunca
  `if (role === ...)` espalhado; ação que o servidor nega não pode aparecer na
  tela (§4.9).

## Fechamento

Rode a skill `gate` e feche com a skill `entrega`. O teste do passo 5 dentro da
suíte de integração exige Docker — a `gate` cobre o pré-voo.
