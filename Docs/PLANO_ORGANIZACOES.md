# Plano de execução — Camada de organizações (multi-BPO)

> **Épico ClickUp:** 86e36ec0q (8 tasks) · **Arquitetura:** 86e32fp4b · **Prazo:** 30/09/2026
> **Escrito em:** 16/09/2026, sobre a `main` em `e673c39` (`develop` idêntica)
> **Status:** PLANO APROVADO em 16/09/2026 (as 7 decisões da §2 respondidas pelo Pedro; ver §2.1). Nenhuma linha de código escrita.

Este documento é o roteiro de implementação. Ele substitui, para fins de execução, o
"Plano de execução" do artifact de 09/09, porque três coisas mudaram desde então (§1). As
leis do CLAUDE.md (§3.15 tenant, §4.8 tenancy, §4.9 matriz) continuam valendo e ganham a
camada de organização na mesma entrega (§9).

---

## 1. O que mudou desde o desenho de 09/09

### 1.1 D1 foi invertida pelo Lucas (09/09, 20h09) e o Pedro aceitou (23h34)

Texto do Lucas: _"platform_admin precisa ver e fazer tudo, pois se algum cliente e/ou
usuário precisar de suporte, é a partir desse acesso que conseguiremos fornecer ajuda."_

O desenho de 09/09 dizia o contrário (plataforma cega para dado de tenant, "dois chapéus =
duas contas"). Com a D1 revisada:

| Antes (09/09)                                 | Agora (D1 revisada)                                                                                                                        |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `scope='platform'` **nega** dado de tenant    | `scope='platform'` **libera tudo**: é a 2ª regra do `resolve_client_access`                                                                |
| Matriz: `platform_admin` só gere organizações | Matriz: `platform_admin` tem ✅ em **todas** as linhas                                                                                     |
| Duas contas para quem opera e administra      | **Uma conta**: quem é plataforma opera qualquer organização                                                                                |
| Navegação da plataforma = só `/organizacoes`  | Navegação = tudo que o admin vê, mais a área de organizações                                                                               |
| Formulários herdam a org da linha do ator     | Para a plataforma, a org do cliente/usuário novo é **escolhida** no formulário (única exceção ao "nunca do payload", validada no servidor) |

Consequências que o épico e o artifact ainda **não** refletem: a linha "D1" da task
86e32fp4b, o bloco "Decisões" do épico, a tabela de permissões e o §4 do artifact.
Atualizar os três é parte da §9 deste plano.

### 1.2 A carteira deixou de ser 1:1 (épico 86e390kku, na `main` desde 15/09)

O épico e o artifact dizem "Carteira exclusiva segue 1:1 (`UNIQUE(client_id)`)". Não é
mais verdade: `client_assignments` tem `is_primary`, `UNIQUE(client_id, user_id)` e o
índice parcial `uq_client_assignments_primary` (migration `6bb85e6b7d72`, que é a **HEAD**
do Alembic). Para esta sprint isso é bom: a validação "gerente e cliente na mesma
organização" entra em **um** lugar, `ClientRepository.is_active_manager`
([apps/api/app/modules/clients/repository.py](../apps/api/app/modules/clients/repository.py)),
consumido por `create_client`, `add_client_manager` e `set_responsible_manager`.

### 1.3 Encerramento de cliente existe (86e36pm1z, na `main`)

Toda rota de escrita em cliente passa por `OpenClientDep`. As rotas novas desta sprint
que escrevem em cliente seguem o mesmo padrão; as de organização ganham o equivalente
(`active=false` na org).

### 1.4 A lista canônica tem 49 endpoints, não 44

`grep -c "SensitiveEndpoint(" apps/api/app/core/sensitive_endpoints.py` → **49**. A bateria
cross-org roda sobre os 49 **mais** os que saem de `NON_TENANT_ENDPOINTS` (§4.3).

### 1.5 O `manager` JÁ cria cliente hoje

`POST /api/v1/clients` usa `ManagerOrAdminDep`
([routes.py:133-149](../apps/api/app/modules/clients/routes.py)) e o service já faz o
criador-gerente virar responsável (`service.py:222-231`). O botão "Novo Cliente" no front
não tem gate nenhum. Ou seja, a metade "criar cliente" da D2 **já está no ar**. O que a D2
realmente adiciona é: `MANAGE_CLIENT_USERS` para o `manager` (dentro da carteira), a
permissão `CREATE_CLIENT` formalizada na matriz (back e front), e o gate do botão.

### 1.6 Dois defeitos pré-existentes que viram vazamento com organizações

- **`GET /api/v1/users` lista TODOS os usuários**, inclusive `scope='client'` de qualquer
  tenant, e `PATCH /users/{id}` edita qualquer um deles
  ([users/service.py:35-47](../apps/api/app/modules/users/service.py)). Hoje é admin-only,
  então não vaza para tenant; com N organizações, o admin da Prospecta veria os usuários da
  Hologram. Corrige-se na task 5 (filtro `scope='system'` + organização).
- **Quatro cópias da regra "admin vê tudo" fora do `authz.py`**, cada uma um vazamento
  independente: `notifications/service.py:172` (`_is_admin`),
  `anomaly_types/service.py:47`, `clients/routes.py:112` (`manager_filter`),
  `reconciliations/routes.py:349` (`role not in {"admin","manager"}`). Todas somem na
  task 2.

---

## 2. Decisões a confirmar ANTES de codar

Cada uma tem recomendação. As respostas mudam o código, então não começo sem elas.

**Q1. As 5 contas da plataforma (Pedro, Lucas, Laio, Taíres, Galhardo).** Com a D1
revisada, plataforma ⊇ admin da org, então a segunda conta perdeu o motivo.
_Recomendo:_ **converter as contas atuais** para `scope='platform'` (a pessoa continua
com o mesmo login; passa a ver a org Hologram e qualquer outra). A org Hologram pode ficar
sem `admin` próprio, porque a plataforma administra. Alternativa: contas separadas.

**Q2. Como a plataforma "vê tudo" na tela.** _Recomendo:_ **visão global**, sem "entrar na
organização": a lista de clientes e a de usuários ganham coluna e filtro "Organização" (só
para plataforma), e os formulários de criação ganham o seletor de organização. É a tela
que o suporte precisa (achar o cliente/usuário sem saber a org). Alternativa: um seletor
de contexto "operando como org X" (mais código, mais estado no front, fica para depois se
o suporte pedir).

**Q3. Como nasce um `platform_admin`.** _Recomendo:_ **só por script**
(`apps/api/scripts/promote_platform_admin.py`, idempotente, por e-mail, proposto), sem
endpoint. O maior privilégio do sistema fica fora da API. D5 ("nunca auto-registro") fica
satisfeita. O seed de dev cria um `platform@hologram.com.br` genérico.

**Q4. Página de tipos de anomalia.** D3 diz escrita só da plataforma. _Recomendo:_ a
página `/configuracoes/anomalias` vira **só plataforma** (o admin da org já vê os nomes
das anomalias na tela de revisão). Alternativa: leitura para admin da org, com botões
ocultos (mais uma variante de tela para manter).

**Q5. Rótulo de autoria mascarada.** Hoje é `"Equipe Hologram"` fixo. _Recomendo:_
**"Equipe {nome da organização do CLIENTE}"**, não do autor: cobre o autor de plataforma
(que não tem org) e, para todo autor de org, coincide, porque a carteira é intra-org.

**Q6. Quem executa.** _Recomendo:_ **eu, task a task, direto nesta conversa**, como no
épico da carteira (86e390kku): branch por task a partir da `develop`, gate local, commit
local, comandos de push e PR entregues, `/code-review high` nas duas tasks críticas (2 e
5). Motivo: é o código mais sensível do sistema, o hub re-semeia a memória a cada run e o
seu último uso foi a Sprint 7 (agosto); desde então tudo saiu por aqui, com menos atrito.
Alternativa: rodar como sprint do agents-hub (exigiria `develop == main`, que hoje vale, e
a sprint-preflight).

**Q7. Estratégia de merge.** Sua pergunta direta. _Recomendo:_ **três ondas**, detalhadas
na §7. Nem "cada task na main" nem "tudo de uma vez".

### 2.1 Respostas do Pedro (16/09/2026)

| #   | Decisão                                                                                                                                                                                                                                          |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Q1  | **Converter as contas atuais** para `scope='platform'`: laio@, taires@, miguel@ (Galhardo), landim@ e pedro@ (domínio hologramgestao.com). A conta de monitoração do smoke (`smoke-monitor@…`) **não** é promovida: segue admin da org Hologram. |
| Q2  | Visão global com coluna e filtro Organização (plataforma) e seletor de org nos formulários de criação.                                                                                                                                           |
| Q3  | `platform_admin` só por script idempotente (`apps/api/scripts/promote_platform_admin.py`). Sem endpoint.                                                                                                                                         |
| Q4  | Página de tipos de anomalia só plataforma.                                                                                                                                                                                                       |
| Q5  | Rótulo "Equipe {nome da org do cliente}". **A org Hologram chama-se "Hologram"** (nome curto, decisão do Pedro), então o texto atual "Equipe Hologram" não muda para os clientes de hoje.                                                        |
| Q6  | Execução por aqui, task a task. Não pelo agents-hub.                                                                                                                                                                                             |
| Q7  | Três ondas (§7).                                                                                                                                                                                                                                 |

**Ajuste ao plano decorrente da Q1:** `POST /system/alert-test` **continua acessível ao
admin da organização** (permissão própria de diagnóstico, `run_alert_test`, com
`platform_admin` + `admin`), e **não** vai para `manage_platform`. Motivo: o job de smoke
do deploy (`scripts/smoke_alert_delivery.py`) loga como o admin de monitoração e chama
essa rota ao fim de todo deploy; a promoção dos cinco só acontece depois da onda 2, e o
deploy da onda 1 falharia no smoke. O alerta sintético sai pelo canal de teste, não pelo
plantão, então o risco de um admin de outra org dispará-lo é baixo. `manage_platform`
fica só com o CRUD de organizações. A matriz da §3.2 passa a ter 13 permissões (65 células).

---

## 3. O modelo final (com a D1 revisada)

### 3.1 Camadas e papéis

```
PLATAFORMA   scope='platform'  role=platform_admin   organization_id NULL   client_id NULL
   │  vê e faz tudo em qualquer organização (D1 revisada); administra organizações
   ▼
ORGANIZAÇÃO  scope='system'    role=admin|manager    organization_id NOT NULL  client_id NULL
   │  admin: tudo DA PRÓPRIA org · manager: carteira (client_assignments) + cria cliente
   │  + gere usuários dos clientes da carteira (D2)
   ▼
CLIENTE      scope='client'    role=client_manager|client_operator   organization_id NOT NULL (desnormalizado)  client_id NOT NULL
      inalterado
```

O valor `scope='system'` é mantido no banco e no JWT (evita migrar linhas e ~40 literais);
só a semântica muda: "system" = staff de UMA organização.

### 3.2 Matriz de permissões (aprovada, 13 permissões × 5 papéis = 65 células)

| Permissão                                 | platform_admin     | admin (org)      | manager (org)          | client_manager | client_operator |
| ----------------------------------------- | ------------------ | ---------------- | ---------------------- | -------------- | --------------- |
| `run_reconciliation`                      | ✅                 | ✅               | ✅                     | ✅             | ✅              |
| `review_export`                           | ✅                 | ✅               | ✅                     | ✅             | ✅              |
| `sync_omie_accounts`                      | ✅                 | ✅               | ✅                     | ✅             | ✅              |
| `manage_glossary`                         | ✅                 | ✅               | ✅ (carteira)          | ✅             | ❌              |
| `manage_client_users` **(D2)**            | ✅                 | ✅               | ✅ (carteira) **novo** | ✅             | ❌              |
| `create_client` **(nova, D2)**            | ✅ (escolhe a org) | ✅               | ✅ (vira responsável)  | ❌             | ❌              |
| `edit_client`                             | ✅                 | ✅               | ❌                     | ❌             | ❌              |
| `view_other_tenant`                       | ✅                 | ✅ (própria org) | ✅ (carteira)          | ❌             | ❌              |
| `manage_org_users` **(nova)**             | ✅                 | ✅ (própria org) | ❌                     | ❌             | ❌              |
| `manage_client_categories` **(nova, D3)** | ✅                 | ✅ (própria org) | ❌                     | ❌             | ❌              |
| `manage_anomaly_types` **(nova, D3)**     | ✅                 | ❌               | ❌                     | ❌             | ❌              |
| `manage_platform` **(nova)**              | ✅                 | ❌               | ❌                     | ❌             | ❌              |
| `run_alert_test` **(nova)**               | ✅                 | ✅               | ❌                     | ❌             | ❌              |

- "(carteira)" e "(própria org)" **não** são células: são `resolve_client_access` e os
  filtros de coleção (§3.4). A célula diz se o papel pode a AÇÃO; o alcance é outra função.
- `manage_org_users` formaliza o que hoje é `AdminDep` em `/users`. `manage_platform`
  cobre o CRUD de organizações. `run_alert_test` cobre o `POST /system/alert-test`
  (platform + admin, ver §2.1).
- Teste novo que codifica a regra do Lucas: **`platform_admin` está em toda linha da
  matriz** (permissão nova sem a célula da plataforma quebra o CI).
- `require_admin` e `require_manager_or_admin` **deixam de existir**: cada uso vira
  guard de permissão da matriz, ou `StaffDep` (proposto: `scope ∈ {platform, system}`)
  para leituras de staff, como `GET /clients` e `GET /client-categories`.

### 3.3 `resolve_client_access`: as cinco regras, nesta ordem

1. `scope='client'` → só o próprio `client_id` da linha. **(inalterado)**
2. `scope='platform'` → **libera**, sem consulta. **(D1 revisada)**
3. `scope='system'` + `admin` → `clients.organization_id == user.organization_id`
   (1 SELECT por PK). **É a linha mais perigosa da sprint**: hoje é `return True`.
4. `scope='system'` + `manager` → existe `client_assignments(client_id, user_id)` **e**
   o cliente é da mesma org (mesma query, com join). Defense-in-depth: o assignment já
   nasce intra-org pela §3.5.
5. Resto → nega.

O teste de precedência (`tests/unit/test_authz_scope_precedence.py`) ganha o caso
"linha corrompida `scope='platform'` com `organization_id` preenchido nega tudo".

### 3.4 Filtros de camada de dados (defense-in-depth do R3, uma camada acima)

- `tenant_filter_client_id` / `scoped_by_tenant`: **inalterados** (escopo de cliente).
- **`scoped_by_reach(stmt, client_id_column, user)`** (proposto): a MESMA decisão da §3.3
  projetada em `WHERE` para coleções endereçadas por `client_id`: plataforma → no-op;
  admin → `EXISTS (clients WHERE id = col AND organization_id = org do ator)`; manager →
  `portfolio_filter` (já existe); cliente → igualdade de tenant. Consumidores: lista de
  clientes (mata `clients/routes.py:112`), notificações (mata `_is_admin`), sessões.
- **`organization_filter_id(user)`** (proposto): plataforma → `None`; demais → a org da
  linha. **`scoped_by_organization(stmt, org_column, user)`** para coleções com coluna de
  org direta: `users`, `client_categories`, `clients`.
- Tabelas por sessão/arquivo/lançamento **não** ganham coluna de org (derivação sempre via
  `clients.organization_id`). Detalhe por PK continua passando por `resolve_client_access`
  dentro de `require_session_access`, que é onde o admin da org B leva 404.

### 3.5 Modelo de dados e migração (uma migration, `down_revision = "6bb85e6b7d72"`)

**Tabela nova `organizations`:** `id` UUID v4, `name` (String 200, `UNIQUE`, comparação
sem caixa no service, como `client_categories`), `active` bool, timestamps. Nome do BPO
é dado de negócio da plataforma, fica em claro (§4.5 cobre dado do cliente final).

**Colunas novas:**

| Tabela              | Coluna                  | Regra                                                                                                                                                                             |
| ------------------- | ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `clients`           | `organization_id`       | FK RESTRICT, **NOT NULL** após backfill, índice                                                                                                                                   |
| `users`             | `organization_id`       | FK RESTRICT, nullable; consistência pelo CHECK abaixo; para `scope='client'` é **desnormalizada** da org do cliente (mesmo racional do §4.11 do primer), preenchida pelo servidor |
| `client_categories` | `organization_id`       | FK RESTRICT, NOT NULL; `uq_client_categories_name` → `UNIQUE(organization_id, name)`                                                                                              |
| `access_audit`      | `actor_organization_id` | nullable, **sem FK** (padrão da tabela), sem backfill                                                                                                                             |

**CHECK ternário em `users`** (substitui `ck_users_scope_client_id`; inclui o papel, para
o banco recusar `platform_admin` com escopo de org, que o Pydantic hoje não pega):

```sql
   (scope = 'platform' AND role = 'platform_admin'
        AND organization_id IS NULL     AND client_id IS NULL)
OR (scope = 'system'   AND role IN ('admin','manager')
        AND organization_id IS NOT NULL AND client_id IS NULL)
OR (scope = 'client'   AND role IN ('client_manager','client_operator')
        AND organization_id IS NOT NULL AND client_id IS NOT NULL)
```

**Ordem do `upgrade()`** (backfill em SQL puro, convergente, como `d5c81a4e9b27`):

1. `organizations` + INSERT "Hologram" (idempotente por nome; nome curto por decisão do Pedro, §2.1).
2. `clients.organization_id` nullable → `UPDATE clients SET organization_id = <Hologram>
WHERE organization_id IS NULL` → `NOT NULL` + FK + índice.
3. `users.organization_id`: `scope='system'` → Hologram; `scope='client'` → org do
   próprio cliente (`UPDATE ... FROM clients`).
4. `client_categories` → Hologram → `NOT NULL`; troca da UNIQUE.
5. **Pré-checagem em plpgsql** (`DO $$ ... RAISE EXCEPTION`, com a query de diagnóstico na
   mensagem, padrão de `6bb85e6b7d72`): aborta se existir linha que violaria o CHECK
   ternário. Só então `DROP` do CHECK antigo + `CREATE` do novo.
6. `access_audit.actor_organization_id`.

**`downgrade()`:** primeira linha é uma guarda plpgsql que **aborta** se existir mais de
uma organização ou qualquer usuário `scope='platform'` (é decisão de dado, não de
schema); depois desfaz na ordem inversa. Round-trip em `tests/integration/test_migrations.py`
(revisões por ID) e teste de drift modelo↔migration para o CHECK (`pg_get_constraintdef`,
padrão de `test_user_tenancy.py:190-209`) e para os nomes das UNIQUEs.

**Regras derivadas:** `is_active_manager` passa a exigir `users.organization_id ==
clients.organization_id`; `clients.category_id` só aceita categoria da mesma org
(validado no service do `PATCH /clients/{id}`); e-mail continua único **global** (409
genérico, como hoje entre tenants).

### 3.6 JWT, `CurrentUser`, login

- `CurrentUser` ganha `organization_id: UUID | None` e `is_platform`; vem da **linha**
  lida em `get_current_user`, como hoje. `get_current_user` passa a negar (401) usuário
  cuja organização está `active=false` (um LEFT JOIN na mesma query).
- Token ganha o claim `organization_id` com default `None` (compatibilidade da Sprint 5:
  token antigo não vira 401). Quem decide é a linha.
- `AuthenticatedUser` (login/refresh, único contrato que o front usa; não existe `/me`)
  ganha `organization_id` e `organization_name`. É o que o header usa para mostrar em
  que chapéu a pessoa está ("Plataforma" ou o nome da org), coisa que hoje nenhuma tela
  mostra.

### 3.7 O que não muda

Cripto por cliente (DEK/KEK/AAD são por cliente; nenhum landmine do §3.14 é tocado),
escrita no Omie, matcher, modelo de tenancy do cliente final, tabelas por sessão,
identidade visual (D4: tudo Hologram; só o rótulo de autoria vira dinâmico).

**Sem feature flag.** O "interruptor" é a existência de uma segunda organização ou de um
usuário de plataforma. Até lá o sistema é idêntico ao de hoje por construção, e é isso
que torna a onda 1 (§7) segura sem flag e sem paridade de env var no deploy.

---

## 4. Inventário do back: tudo que muda, por arquivo

### 4.1 Core

| Arquivo                                       | Mudança                                                                                                                                                                                                                                                                                                        |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/db/models/organization.py` **(novo)**    | modelo `Organization`                                                                                                                                                                                                                                                                                          |
| `app/db/models/user.py`                       | `UserScope.PLATFORM`, `UserRole.PLATFORM_ADMIN`, `PLATFORM_ROLES`, `organization_id`, constantes do CHECK ternário (LABEL + predicado; a migration COPIA)                                                                                                                                                      |
| `app/db/models/client.py`                     | `organization_id` + relationship                                                                                                                                                                                                                                                                               |
| `app/db/models/client_category.py`            | `organization_id`, UNIQUE composta                                                                                                                                                                                                                                                                             |
| `app/db/models/access_audit.py`               | `actor_organization_id`                                                                                                                                                                                                                                                                                        |
| `app/db/models/__init__.py`                   | exports (o autogenerate só enxerga o que está aqui)                                                                                                                                                                                                                                                            |
| `alembic/versions/<nova>_s8_organizations.py` | migration da §3.5                                                                                                                                                                                                                                                                                              |
| `app/core/authz.py`                           | `CurrentUser.organization_id`/`is_platform`; 4 permissões novas; `PLATFORM_ADMIN` em toda célula; `resolve_client_access` com as 5 regras; `organization_filter_id`, `scoped_by_organization`, `scoped_by_reach`                                                                                               |
| `app/core/dependencies.py`                    | `get_current_user` (org + org ativa); **remove** `require_admin`/`require_manager_or_admin`; `StaffDep`; guards novos `CreateClientDep`, `ManageOrgUsersDep`, `ManageClientCategoriesDep`, `ManageAnomalyTypesDep`, `ManagePlatformDep`, `RunAlertTestDep`; `deny_client_access` passa `actor_organization_id` |
| `app/core/audit.py`, `app/core/telemetry.py`  | `actor_organization_id` na trilha e `organizacao_ator` na telemetria                                                                                                                                                                                                                                           |
| `app/core/security.py`                        | claim `organization_id` (default `None`)                                                                                                                                                                                                                                                                       |
| `app/core/sensitive_endpoints.py`             | ver §4.3                                                                                                                                                                                                                                                                                                       |
| `app/core/exceptions.py`                      | `OrganizationNotFoundError`, `OrganizationInactiveError`, `OrganizationNameAlreadyExistsError`, `CrossOrganizationError` (409, "gerente de outra organização")                                                                                                                                                 |

### 4.2 Módulos

| Módulo                                                    | Mudança                                                                                                                                                                                                                                                                                                                                                                                                                         |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `organizations/` **(novo)**                               | `routes/service/repository/schemas`: `GET/POST /organizations`, `GET/PATCH /organizations/{organization_id}` (nome, `active`), contagens de clientes/usuários; `usage_events` `organizacao_criada` e `organizacao_desativada` (sem dedup, como todo evento novo)                                                                                                                                                                |
| `auth/`                                                   | `_issue_tokens` e `to_authenticated_user` carregam org (id + nome)                                                                                                                                                                                                                                                                                                                                                              |
| `users/`                                                  | `GET /users` filtra `scope='system'` **e** org (admin: própria; plataforma: todas, `?organizationId=` opcional, `?role=` opcional para o seletor de gerentes); `POST /users`: org da linha do ator, ou do body **só** para plataforma (validada: existe e ativa); `UserResponse` ganha `scope`, `organization_id`, `organization_name`; `client_routes.py` inalterado (a célula nova da matriz já libera o manager da carteira) |
| `clients/`                                                | `routes.py:112` some (vira `scoped_by_reach`); `POST` com `CreateClientDep`, org da linha ou do body (plataforma); `ClientResponse` ganha `organization`; `is_active_manager` exige mesma org; `PATCH` valida categoria da mesma org; lista aceita `?organizationId=` (plataforma)                                                                                                                                              |
| `client_categories/`                                      | org-scoped: lista por org, escrita `ManageClientCategoriesDep`, unicidade por org                                                                                                                                                                                                                                                                                                                                               |
| `anomaly_types/`                                          | escrita `ManageAnomalyTypesDep`; `_effective_include_inactive` consulta a permissão, não `role == "admin"`                                                                                                                                                                                                                                                                                                                      |
| `notifications/`                                          | `_is_admin` some; `_visibility_filter` usa `scoped_by_reach`                                                                                                                                                                                                                                                                                                                                                                    |
| `reconciliations/`                                        | `routes.py:349` vira `StaffDep`; `author_for_viewer(author, viewer, organization_name)` com o rótulo "Equipe {org do cliente}" nos 3 call sites (detalhe, export, lista do cliente); `tenant_scope.audit_session_tenant_miss` passa a auditar também staff cross-org                                                                                                                                                            |
| `system/`                                                 | `alert-test` com `RunAlertTestDep` (platform + admin; o smoke do deploy depende disso)                                                                                                                                                                                                                                                                                                                                          |
| `scripts/seed_dev.py`                                     | `seed_organization` (get-or-create "Hologram"), admin ligado a ela, `seed_platform_admin` (`platform@hologram.com.br`, dev)                                                                                                                                                                                                                                                                                                     |
| `scripts/promote_platform_admin.py` **(novo)**            | por e-mail, idempotente; recusa `scope='client'`; zera `organization_id` e `client_id`                                                                                                                                                                                                                                                                                                                                          |
| `scripts/seed_demo_client.py`, `seed_sprint6_scenario.py` | "primeiro admin/manager ativo" passa a ser "da org do cliente demo"                                                                                                                                                                                                                                                                                                                                                             |
| `scripts/gen_sensitive_endpoints_doc.py`                  | rodar após mexer na lista (a página é derivada)                                                                                                                                                                                                                                                                                                                                                                                 |

### 4.3 Lista canônica e bateria cross-org

- **Saem de `NON_TENANT_ENDPOINTS` e entram em `SENSITIVE_ENDPOINTS`** (sensíveis a
  organização): `GET/POST /clients`, `PATCH /clients/{client_id}`, os 6 de `/users*`, os 4
  de `/client-categories*`. Entram como NON_TENANT com motivo: os 4 de `/organizations*`
  (plataforma, sem dado de cliente). Ficam: auth, anomaly-types, alert-test (platform + admin),
  test-connection.
- **Bateria:** a fixture `tenants` de `test_sensitive_endpoints.py:227` ganha `org_a`,
  `org_b`, `admin_b` e `manager_b`; o `parametrize` ganha o **ator** como segunda
  dimensão (`operador_a`, `admin_b`, `manager_b`) reusando `_BODIES`, `_QUERIES`,
  `_substitute_deep` e a asserção dupla (`SECRET_NAME_B not in resp.text` +
  `status in {403, 404}`). `test_admin_continua_alcancando_os_dois_tenants` vira dois:
  "plataforma alcança as duas orgs" e "admin alcança só a própria".
- **Trilha:** `test_tenant_isolation.py` ganha "admin da org B negado em cliente da org A
  grava 1 linha com `user_scope='system'` e `actor_organization_id = org_b`".

---

## 5. Inventário do front

| Arquivo                                                                                                                                                                     | Mudança                                                                                                                                                                                                                                                                                                     |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/lib/contracts/schema.ts`                                                                                                                                               | regenerado (`pnpm --filter @auditoria/web gen:types` com a API no ar); `Record<UserRole, …>` em `authz.ts` quebra o `tsc` de propósito até a matriz ganhar o papel                                                                                                                                          |
| `src/lib/authz.ts`                                                                                                                                                          | 4 permissões novas; coluna `platform_admin`; `isStaff` (platform ou system); `canAccessClient` → staff libera; `canSeeSystemArea` → staff; `canManageSystemUsers` vira `hasPermission('manage_org_users')`; `USER_ROLE_LABELS` += "Administrador da plataforma"; `homePathFor` segue `/clientes` para staff |
| `src/lib/api/users.ts`, `src/lib/validation/users.ts`                                                                                                                       | união de papéis **do contrato** (`SystemUserRole`), não redigitada                                                                                                                                                                                                                                          |
| `src/components/features/navigation/nav-items.tsx`                                                                                                                          | seção Configurações por permissão: Usuários (`manage_org_users`), Categorias (`manage_client_categories`), Tipos de Anomalia (`manage_anomaly_types`), **Organizações** (`manage_platform`)                                                                                                                 |
| `src/app/(app)/configuracoes/organizacoes/` **(novo)**                                                                                                                      | lista (nome, ativa, nº clientes, nº usuários), criar, editar/ativar/desativar; reusa os componentes de `features/users/`                                                                                                                                                                                    |
| `src/app/(app)/clientes/page.tsx`, `create-client-modal.tsx`                                                                                                                | botão "Novo Cliente" com `create_client`; coluna + filtro Organização e seletor de org no modal (só plataforma)                                                                                                                                                                                             |
| `src/app/(app)/configuracoes/usuarios/page.tsx`, `create-user-modal.tsx`, `edit-user-modal.tsx`, `user-badges.tsx`                                                          | coluna + filtro Organização (plataforma); seletor de org ao criar (plataforma); badge para `platform_admin`                                                                                                                                                                                                 |
| `client-managers-section.tsx`                                                                                                                                               | candidatos via `GET /users?role=manager&organizationId=<org do cliente>` em vez de filtrar `role === 'manager'` no cliente                                                                                                                                                                                  |
| `src/app/(app)/layout.tsx`                                                                                                                                                  | header mostra "Plataforma" ou o nome da organização ao lado do papel                                                                                                                                                                                                                                        |
| `anomaly-types-page.tsx`, `client-categories-page.tsx`                                                                                                                      | gates pelas permissões novas                                                                                                                                                                                                                                                                                |
| 6 strings "da Hologram" (`clientes/page.tsx:135`, `usuarios/page.tsx:105,124`, `anomaly-types-page.tsx:86`, `client-categories-page.tsx:45`, `create-client-modal.tsx:197`) | copy neutra ("da sua organização" / "da plataforma"). Login, header, tema e logomark **não mudam** (D4)                                                                                                                                                                                                     |
| Testes vitest                                                                                                                                                               | `authz.test.ts` (fixtures + matriz), `sidebar-nav.test.tsx` e `client-users-screen.test.tsx` (manager passa a ver Usuários do cliente da carteira), novos para organizações                                                                                                                                 |
| `e2e/a11y-mocked.spec.ts`                                                                                                                                                   | `PROFILES` ganha `platform` e o `manager-sistema` vira `clientUsers: true`; usuários mockados ganham `organization_id`/`organization_name`; gate nos 3 temas + screenshots desktop e 390px                                                                                                                  |

---

## 6. As 8 tasks: escopo, arquivos, critérios de aceite

A ordem é a do épico. Cada task: branch própria a partir da `develop`
(`feat/S8-<slug>`), gate local verde citado com output, commit local em Conventional
Commits (EN-US), PR em PT-BR entregue pronto. Status IN PROGRESS no ClickUp ao começar.

### Task 1 · 86e36ec7p · BACK Fundação de dados

- **Toca:** modelos (§4.1), migration, `__init__.py`, seeds, `test_migrations.py`, teste
  de drift do CHECK e das UNIQUEs.
- **Aceite:** `alembic upgrade → downgrade → upgrade` verde no round-trip; backfill
  idempotente (2 ciclos); `INSERT` que viola o CHECK ternário levanta `IntegrityError`;
  `pnpm db:seed` cria a org Hologram e liga o admin; **nenhum** comportamento de API muda
  (a suíte inteira continua verde sem tocar em teste de rota).

### Task 2 · 86e36ecar · BACK Authz core ⚠️ crítica

- **Toca:** `authz.py`, `dependencies.py`, `security.py`, `audit.py`, `telemetry.py`,
  `auth/`, e os 4 lugares com cópia da regra (§1.6).
- **Aceite:** `test_authz_matrix.py` com as 65 células transcritas + "plataforma em toda
  linha"; precedência com os casos de plataforma; `resolve_client_access` com teste
  unitário por regra; `require_admin`/`require_manager_or_admin` não existem mais (grep
  zero); nenhuma rota compara `role`/`scope` na mão (grep zero fora do `authz.py`).
- `/code-review high` antes do PR.

### Task 3 · 86e36ecjp · BACK Expansão do manager (D2)

- **Toca:** células `manage_client_users` e `create_client`; `POST /clients` com
  `CreateClientDep`; `is_active_manager` intra-org; testes de bloqueio refeitos
  (o manager da carteira passa nas 6 rotas de `client_routes.py`; fora da carteira, 404).
- **Aceite:** teste "manager cria cliente e vira responsável" (já existe, confirmar) e
  "manager de outra org não entra na carteira" (409 `CrossOrganizationError`).

### Task 4 · 86e36ecnp · BACK Módulo organizations

- **Toca:** módulo novo, `sensitive_endpoints.py` (§4.3), bateria cross-org, doc derivada.
- **Aceite:** 4 rotas platform-only (admin da org recebe 403 sem corpo que nomeie outra
  org); desativar org → usuários dela recebem 401 no request seguinte; bateria cross-org
  verde em **todos** os endpoints sensíveis (≥ 49 + os reclassificados);
  `test_toda_rota_da_api_esta_classificada` verde; `endpoints-sensiveis-sprint5.md`
  regenerado.

### Task 5 · 86e36ecqz · BACK Rotas existentes org-aware ⚠️ crítica

- **Toca:** `users/`, `clients/`, `client_categories/`, `anomaly_types/`,
  `notifications/`, `reconciliations/` (rótulo + `tenant_scope`), `promote_platform_admin.py`.
- **Aceite:** `GET /users` nunca devolve `scope='client'` nem outra org; admin da org B
  não lista/edita usuário, cliente, categoria nem notificação da org A (tudo dentro da
  bateria da task 4); usuário de cliente da org X lê "Equipe X"; categoria de outra org no
  `PATCH /clients/{id}` → 422/404; script de promoção idempotente com teste.
- `/code-review high` antes do PR.

### Task 6 · 86e36ecwa · FRONT Área da plataforma

- **Toca:** contratos, `authz.ts`, nav, `/configuracoes/organizacoes`, header.
- **Aceite:** `authz.test.ts` com a matriz espelhada; plataforma vê Organizações e entra
  em qualquer cliente; admin da org não vê Organizações; a11y nos 3 temas; screenshots
  desktop e 390px abertos e conferidos (gate verde não prova layout).

### Task 7 · 86e36ed1d · FRONT Telas existentes org-aware

- **Toca:** clientes (coluna/filtro/seletor), usuários (idem + badge), gerentes com acesso,
  categorias, tipos de anomalia, copy neutra, gate do "Novo Cliente".
- **Aceite:** manager da carteira vê "Usuários" dentro do cliente e o botão "Novo
  Cliente"; `client_operator` não; plataforma escolhe a org ao criar cliente e usuário;
  nenhuma string "da Hologram" em tela de dado (grep); a11y 3 temas + screenshots.

### Task 8 · 86e36ed4b · QA/DEPLOY Cenário Prospecta ponta a ponta

- **Toca:** dev no ar (onda 2 deployada), e2e `PROFILES`, CLAUDE.md, PRD, memória, artifact.
- **Roteiro em dev:** promover os 5 → criar org "Prospecta" → criar admin dela → como
  esse admin: criar cliente, operador, rodar conciliação com o cliente demo mockado →
  como admin da Hologram: confirmar que nada da Prospecta aparece (lista, busca, URL
  direta = 404) → como plataforma: ver as duas → trilha `access_audit` com a negação.
- **Aceite:** §9 inteira feita.

---

## 7. Estratégia de merge e deploy: três ondas

Fatos que regem a resposta: o CI (`ci.yml`) roda em **PR para `main`** e push na `main`,
não em PR para `develop`; o deploy de dev (`deploy-dev.yml`) roda em **todo push na
`main`**, com a migration **antes** da API; e o ambiente de dev é o que a Bruna, o Murilo e
o Galhardo usam todo dia. Logo, "mandar para a `main`" = "colocar no ar para o time".

**Por que não "cada task na main":** 8 deploys, com o back sabendo de `platform` dias
antes de o front saber (uma conta promovida cedo demais seria trancada fora de todo
cliente, porque `canAccessClient` no front devolve `false` para escopo desconhecido), e a
célula nova do manager no servidor antes do gate na tela.

**Por que não "tudo de uma vez":** um PR de ~45 arquivos de back + ~20 de front + migration

- reescrita de authz + telas novas, num deploy só. Se algo quebrar em dev, tudo é suspeito,
  e o rollback é reverter uma migration com dado possivelmente já criado por cima. Também
  tranca a `develop` por duas semanas: qualquer hotfix que precisasse ir para a `main`
  levaria a sprint inteira junto.

**Recomendação: três ondas, cada uma um `develop → main`.** Dentro da onda, cada task é
um PR para a `develop` (revisável, ~5 a 12 arquivos); a onda é o ponto de integração
onde o CI roda e o deploy acontece. Entre ondas, `develop == main` de novo, então hotfix
flui normalmente.

| Onda                   | Conteúdo        | O que muda em dev                                                                                                                                                                                                                                                | Rollback                                                  |
| ---------------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| **1 · back invisível** | tasks 1→2→3→4→5 | **Nada visível.** Uma org (Hologram), zero usuários de plataforma. É o canary mais seguro possível para a migration e para a reescrita do `resolve_client_access`: dois ou três dias de uso real com o schema novo e a regra nova, sem mudança de comportamento. | `alembic downgrade` (a guarda deixa: 1 org, 0 plataforma) |
| **2 · front**          | tasks 6→7       | Telas novas e gates; depois do deploy, **promoção dos 5** pelo script → passam a ver "Plataforma" no header e o menu Organizações. Managers da Hologram passam a ver "Usuários" nos clientes da carteira (**D2, comunicar antes**).                              | reverter o deploy do web; despromover pelo script         |
| **3 · cutover**        | task 8          | Org Prospecta criada em dev (cadastro real, não seed), docs, PRD, memória, épico fechado.                                                                                                                                                                        | n/a                                                       |

Regras da onda: (a) a onda só vai para a `main` com gate local verde citado E CI verde no
PR `develop → main`; (b) a `develop` nunca fica mais de ~3 dias à frente da `main`; (c)
se um hotfix urgente aparecer no meio da onda 1, ele vai junto, e é seguro por
construção (comportamento idêntico); (d) a promoção dos 5 acontece **só** depois da onda
2 no ar.

---

## 8. Riscos e armadilhas já mapeadas

1. **`admin → True`** vira checagem de org (§3.3). Esquecer = vazamento entre BPOs por
   qualquer admin. Coberto pela bateria cross-org com ator `admin_b`.
2. **As 4 cópias da regra** (§1.6) e o `GET /users` sem filtro. Coberto por grep zero +
   testes de rota.
3. **`NON_TENANT_ENDPOINTS` como esconderijo**: rota que ficar lá não entra na bateria.
   A reclassificação da §4.3 é obrigatória na task 4, e `test_rota_com_parametro_de_tenant_e_sempre_sensivel`
   continua vigiando `{client_id}`.
4. **CHECK com papel**: se existir linha inconsistente em dev, a migration aborta com a
   query de diagnóstico, em vez de falhar cega. Conferir em dev antes da onda 1 com
   `SELECT id, scope, role, client_id FROM users WHERE ...` (a mesma query da guarda).
5. **Promover cedo demais** tranca a conta fora dos clientes (front sem `platform`). Regra
   (d) da §7.
6. **`client_categories`**: troca de UNIQUE + `clients.category_id` cruzando org. Validado
   no service, testado.
7. **Notificações sem FK**: filtro por org via `EXISTS` em `clients`; linha órfã de
   cliente apagado continua invisível como hoje.
8. **`user_client_favorites` e `reconciliation_sessions.created_by`** são pares
   usuário×cliente: um usuário movido de org deixaria linhas cross-org. Não há operação
   "mover de org" nesta sprint; o script de promoção só muda para plataforma (que alcança
   tudo). Registrar como limite. **Atualização 21/09/2026 (task 86e3bvbfx): a operação
   existe — `POST /users/{id}/transfer`, só plataforma — e trata os pares: carteira de
   colaborador em cliente aberto e favoritos cross-org são removidos; responsável de cliente
   aberto recusa (409); `created_by`/`assigned_by` ficam como histórico.**
9. **Seeds e e2e reais** (`a11y.spec.ts`) usam `admin@hologram.com.br`, que continua admin
   da org Hologram: nada quebra.
10. **`SystemUserRole`** segue `admin|manager`: `platform_admin` **não** entra em nenhuma
    whitelist de API (Q3). Tentativa de forjar via `/users` → 422.
11. **Rate limit e telemetria** não ganham dimensão de org nesta sprint (limite conhecido).

---

## 9. Definition of Done da sprint (além do código)

Na **mesma entrega** da task 8, não depois:

- **CLAUDE.md v1.27**: nota de status (Sprint 8), §3.15 (plataforma libera; admin exige org;
  contagem da lista com o comando), §4.8 (3 escopos + CHECK ternário), §4.9 (matriz da
  §3.2), §8 (tabela de sprints), rodapé.
- **ClickUp**: task 86e32fp4b (linha D1 revisada + limites sem o "1:1"), épico (idem,
  49, ondas), as 8 subtasks com descrição (hoje **vazias**), PRD consolidado
  (bloco novo na página 8cgq1xb-12437, via API v3 com o token do hub).
- **Artifact** 21f4bbdc republicado com a D1 revisada, a matriz nova e este plano.
- **Memória** do projeto atualizada.
- `apps/api/docs/endpoints-sensiveis-sprint5.md` regenerado.

---

## 10. Estimativa (é estimativa; base explícita)

Base: o épico da carteira (86e390kku, 2 tasks, ~10 arquivos, 15/09) levou 31 min de
implementação do back e ~62 min do front, mais uma rodada de `/code-review high` e o
ciclo de push/PR/deploy do Pedro. Esta sprint tem ~5× o raio (≈ 45 arquivos de back, 20 de
front, 10+ suítes), com duas tasks que pedem revisão profunda.

| Onda              | Sessões de trabalho (implementação + revisão + gate) |
| ----------------- | ---------------------------------------------------- |
| 1 (tasks 1 a 5)   | 3 a 4                                                |
| 2 (tasks 6 e 7)   | 2 a 3                                                |
| 3 (task 8 + docs) | 1 a 2                                                |

Cabe no prazo de 30/09 se a onda 1 começar até 18/09 e o ciclo de PR/deploy de cada onda
fechar no mesmo dia do gate verde.
