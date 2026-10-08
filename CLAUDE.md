# CLAUDE.md — Hologram OS (Hologram Gestão)

> **Para futuras conversas com Claude:** este arquivo é o _primer_ obrigatório. Leia-o antes de qualquer ação. Ele é atualizado continuamente conforme decisões são tomadas.
>
> **Status do projeto:** 🚀 **S0–S19 + Sprints 0–5 do agents-hub estão na `main` e rodando em dev** no Google Cloud Run (GCP `liberdade-assessoria`, região `southamerica-east1`). Acesso pelas URLs `*.run.app` via **BFF reverse-proxy do Next** — não há custom domain (o BFF resolveu o cookie cross-site, então o DNS na Wix nunca foi necessário). **Não trate mais como greenfield:** o código é a fonte da verdade — leia antes de assumir que algo "ainda precisa ser criado".
>
> ⚠️ **O sistema é MULTI-TENANT desde a Sprint 5 e MULTI-ORGANIZAÇÃO desde o épico 86e36ec0q.** Usuários do cliente final logam e enxergam **apenas o próprio tenant**; staff de uma organização alcança **apenas os clientes dela**. Antes de escrever qualquer query, endpoint ou tela que toque dado escopável, leia **§3.15 (autorização por tenant e por organização)**, **§4.8 (modelo de tenancy)** e **§4.9 (matriz de permissões)**. Endpoint novo que esqueça o filtro é vazamento entre clientes **ou entre BPOs** — a lista canônica está em **120/120** (`grep -c "SensitiveEndpoint(" apps/api/app/core/sensitive_endpoints.py`) e essa cobertura não pode regredir.
>
> **O que cada sprint do agents-hub entregou** (todas na `main`):
>
> | Sprint | Entrega                                                                                                      |
> | ------ | ------------------------------------------------------------------------------------------------------------ |
> | **1**  | Conciliação de **fatura de cartão** + conta aplicação/CDB (`account_type`)                                   |
> | **2**  | Fim da perda silenciosa de dado no parsing (CSV grande, XLSX truncado)                                       |
> | **3**  | **Cripto por cliente** (DEK+KEK), `access_audit`, alerting fail-closed                                       |
> | **4**  | Lista de conciliações, **gaveta** de criação, **multi-arquivo**, notificações in-app, `usage_events`         |
> | **5**  | **Multi-tenancy**: `users.scope`/`client_id`, papéis de cliente, matriz de permissões, isolamento por tenant |
> | **6**  | **Glossário por cliente** (tabela cifrada por tenant, `glossary_version`), veredito do revisor               |
> | **7**  | **Escrita no Omie**: lançamento das compras `sem_omie` da fatura de cartão (`IncluirLancCC`)                 |
>
> ⚠️ **O invariante "Omie read-only" acabou na Sprint 7.** O ADL agora **grava movimento
> financeiro na contabilidade do cliente**. Antes de encostar nesse fluxo, leia **§3.16**:
> ele nasce **desligado** (`OMIE_POSTING_ENABLED=false`), o contrato do `IncluirLancCC`
> segue **NÃO-VERIFICADO** contra a API real, e o critério de rollback é **um único
> lançamento duplicado em produção**.
>
> **Roadmap (PRD 15/06/2026 → FASE 0–5):** plano em [Docs/PLANO_PROXIMOS_PASSOS.md](Docs/PLANO_PROXIMOS_PASSOS.md). **FASE 0 ✅** (Redis/ARQ removido, `BackgroundTasks`). **FASE 1 ✅ na `main`** — tolerância de data fixa (`DATE_DIVERGENCE_RANGE = 3` em `processing/matcher.py`) e cartão. **FASE 2** lançamento de fatura no Omie (Sprint 7). **FASE 3** glossário por cliente (**Sprint 6 — a próxima**). **FASE 4** Open Finance (Pluggy). **FASE 5** rotinas automáticas de auditoria.

---

## 1. Contexto Rápido

**O que é:** o **Hologram OS**, plataforma para conciliar, classificar e fechar o financeiro de cada cliente: nasceu como auditoria de lançamentos bancários contra o ERP Omie e hoje vai até o arquivo contábil. O nome é de 30/09/2026 (decisão do Pedro); antes era "Sistema de Auditoria de Lançamentos", sigla **ADL**, que continua em comentário, histórico, identificador (`ADL-PARSE-*`, prefixo do `cCodIntLanc`) e no canal do Slack, mas nunca em texto de tela. **Domínio: NÃO decidido** (opções na mesa: `hologramos.com.br`, `hgos.com.br`, `holos.app.br`…; nenhum registrado) — o `PRODUCT_DOMAIN = 'hologramos.com.br'` do código é placeholder da primeira opção, e nenhuma URL é montada a partir dele (decisão e compra: task 86e3fr9wm).

**Fluxo núcleo:**

1. Analista faz upload de extrato/fatura → 2. IA (Claude) extrai movimentações → 3. Humano valida amostra → 4. Sistema busca lançamentos Omie e faz matching determinístico → 5. Humano revisa → 6. Relatório Excel gerado.

**É multi-organização desde o épico 86e36ec0q** (até 09/2026 não era): a plataforma hospeda **organizações** (BPOs e escritórios de contabilidade), cada uma com os próprios clientes finais, staff e catálogo de categorias. A **Hologram Gestão** é a **primeira** organização, não a dona do sistema: o produto é o Hologram OS, a empresa é uma das organizações dele. "Multi-cliente" segue significando múltiplos clientes finais **de uma organização**. Ver §4.8.

**Fontes da verdade:**

- **Funcional:** `Docs/documentation/` (arquivos 0 a 18, numerados sequencialmente).
- **Backlog:** `Docs/List _ Auditora de Lançamentos - Backlog _ Hologram (Lista) - TAREFAS.pdf`.
- **PRD vigente (roadmap):** `Docs/NextSteps/PRD - Próximos Passos-20260615173056.md` — FASE 0–5 (estabilização, cartão, glossário, Pluggy, rotinas).
- **Plano de execução vigente:** [Docs/PLANO_PROXIMOS_PASSOS.md](Docs/PLANO_PROXIMOS_PASSOS.md) — sessões **S20+** derivadas do PRD. **É o plano ativo daqui pra frente.**
- **Plano histórico (S0–S19):** [Docs/PLANO_IMPLEMENTACAO.md](Docs/PLANO_IMPLEMENTACAO.md) — conciliação file-driven, já construída.
- **Plano antigo do pivot (SUPERSEDED):** [Docs/PLANO_S20_AUDITORIA_CONTINUA.md](Docs/PLANO_S20_AUDITORIA_CONTINUA.md) — auditoria contínua; **absorvido na FASE 5** do plano vigente. Útil só como material de origem (rastreabilidade dos transcritos + modelo de dados).
- **Fluxograma:** `Docs/flow/Fluxograma Completo - sistema de conciliação.png`.
- **Subprocessadores e LGPD:** [Docs/seguranca/SUBPROCESSADORES.md](Docs/seguranca/SUBPROCESSADORES.md): quem recebe dado do cliente (Anthropic, Google Cloud, Omie, Slack), o que vai para cada um, os termos com fonte e data, e o que não sai da plataforma. Chamada nova a serviço externo entra lá na mesma entrega.

**Convenção de IDs de tarefa:** quando o usuário citar `[BACK 1.1]` ou `[FRONT 9.12]`, isso vem do PDF do backlog. Mapeie para a sessão correspondente (S3, S12, etc.) consultando o PLANO.

---

## 2. Stack (decisões formalizadas)

**Decisões operacionais (24/04/2026):** FastAPI + uv + pnpm + monorepo simples. _O ARQ/Redis foi removido na FASE 0 (16/06/2026) — background jobs agora rodam via `BackgroundTasks` nativo do FastAPI._

### Backend

- **Python 3.12+** gerenciado via **`uv`** (workspaces habilitados)
- **FastAPI 0.115+**
- **SQLAlchemy 2.0** (async) + **Alembic**
- **PostgreSQL 16** + **psycopg3** async
- **Pydantic v2** (DTOs + settings)
- **httpx** (async HTTP client)
- **`BackgroundTasks` nativo do FastAPI** para o processamento assíncrono da conciliação (Omie + matching + qualificação) — sem broker, sem Redis (FASE 0). Teto via `asyncio.timeout(RECONCILIATION_TIMEOUT_SECONDS)`; rede de segurança no cron `mark_stuck_sessions_as_error`.
- **cryptography** para AES-256-GCM
- **python-jose** para JWT, **bcrypt** direto (cost ≥ 12) — passlib não é usado (incompatível com bcrypt 5.x)
- **openpyxl** para Excel
- **structlog** para logs estruturados
- **pytest + pytest-asyncio + respx + testcontainers + hypothesis** (property-based)
- **ruff (lint + format) + mypy strict**

### Frontend

- **Next.js 14 App Router** + **TypeScript strict**
- **Node 20 LTS** gerenciado via **`pnpm`** (workspaces habilitados)
- **TailwindCSS** + **shadcn/ui**
- **TanStack Query v5** + **Zustand**
- **react-hook-form + zod**
- **@tanstack/react-table + @tanstack/react-virtual**
- **date-fns**
- **vitest + react-testing-library + playwright**

### Estrutura do repositório

- **Monorepo simples** (1 repo no GitHub) com `apps/api` + `apps/web` + `packages/shared-types`
- Orquestração via **scripts pnpm na raiz** (`pnpm dev:api`, `dev:web`, `infra:up`, `db:migrate`, `db:seed`, …). Há um `Makefile`, mas `make` não está disponível no ambiente Windows do dev — **prefira os scripts pnpm** (ver `MEMORY.md`).
- Deploys independentes via **path filters** no GitHub Actions

### Infra

- **Dev local:** Docker Compose (`docker/docker-compose.yml`) sobe **Postgres** (sem Redis desde a FASE 0).
- **Deploy (dev):** **Google Cloud Run** no GCP `liberdade-assessoria`, região `southamerica-east1`. Imagens no **Artifact Registry** (`southamerica-east1-docker.pkg.dev`), build via **Cloud Build**. Serviços: API e web; migration/cleanup rodam como **Cloud Run Jobs**. **Sem worker e sem Redis/Upstash** (FASE 0). ⚠️ A API precisa de **`--no-cpu-throttling` + `min-instances ≥ 1`** porque o processamento roda em `BackgroundTasks` fora do handler HTTP (ver §10).
- **CI/CD:** GitHub Actions — `ci.yml` (qualidade) + `deploy-dev.yml` / `deploy-prod.yml`.
- **Observabilidade:** Sentry + Grafana/Loki.

---

## 3. Regras Invioláveis de Segurança

**Estas regras valem para 100 % do código. Nunca as viole, mesmo que o usuário peça.**

1. **Nunca** armazene `OMIE_ENCRYPTION_KEY`, `JWT_SECRET`, `ANTHROPIC_API_KEY`, `ALERT_WEBHOOK_URL` — nem o material da **KEK/DEK** (Sprint 3) — em código, banco, log ou resposta. Apenas env vars / GCP Secret Manager / Cloud KMS. O `alerting` loga só o `code` do alerta, nunca a URL do webhook.
2. **Nunca** retorne hash de senha, credenciais descriptografadas ou tokens em respostas de API.
3. **Nunca** logue: senhas, credenciais Omie, JWTs, conteúdo de arquivos. Use `[REDACTED]`. O redactor do structlog (em `apps/api/app/core/logging.py`) mascara automaticamente em duas camadas: por NOME de chave (o valor inteiro vira `[REDACTED]`) e por FORMA do valor, sob qualquer chave, inclusive o `event` e o texto da exceção (só o trecho casado vira `[REDACTED]`): JWT, chave da Anthropic (`sk-ant-`), URL de webhook do Slack e do Discord, `Authorization: Bearer`/`Basic` e chave hex de 64 caracteres. Exceção: o 64 hex NÃO mascara sob chave cujo nome contém `hash`, `sha` ou `digest`, porque `file_hash` e `sha256` são identificadores que o suporte lê. Ele roda depois do `format_exc_info`, e a varredura é linear (padrão com quantificador opcional no início é O(n²): teste de 100 KB sem espaço no `test_logging.py`).
4. **Nunca** use `float` para valores monetários. Sempre `Decimal` (Python) ou string/BigInt de centavos (TS). `DECIMAL(14,2)` no DB.
5. **Nunca** use IDs sequenciais em rotas públicas. Sempre UUID v4.
6. **Nunca** acesse `session` / DB global. Sempre via `Depends` do FastAPI.
7. **Nunca** escreva SQL cru. Se inevitável, use `text()` + `bindparams`.
8. **Nunca** confie em validação client-side. Revalide tudo no servidor (extensão, tamanho, magic bytes, hash, RBAC).
9. **Nunca** retorne "senha incorreta" ou "email não existe" separadamente no login — resposta genérica "E-mail ou senha incorretos".
10. **Nunca** faça upload de arquivo para disco. Processar em memória e descartar.
11. **Nunca** permita que manager veja cliente fora da própria carteira. Sempre validar `client_assignments` — **qualquer** linha `(client_id, user_id)` concede acesso, responsável ou colaborador (§4.13).
12. **Nunca** confie em token JWT sem revalidar `users.active = true` no DB (middleware) — usuário desativado perde acesso instantaneamente. A mesma leitura confere `users.password_changed_at`: token com `iat` anterior ao carimbo é recusado, no access e no refresh. O carimbo tem **DOIS gatilhos**, e nenhum segundo mecanismo (sem tabela de `jti`, sem coluna nova): a **redefinição de senha pela plataforma** (86e3ewukz: hash novo + carimbo; o alvo entra de novo com a senha nova) e o **encerramento de sessões por quem gere o usuário** (86e3anx4u: SÓ o carimbo; hash e `active` intocados, o alvo entra de novo com a MESMA senha). O encerramento tem uma rota por família, `POST /users/{id}/sessions/revoke` (staff, `manage_org_users`) e `POST /clients/{id}/users/{user_id}/sessions/revoke` (usuário do cliente, `manage_client_users`), e a própria sessão é 409 (para sair da própria conta existe o logout). O `iat` é inteiro e a comparação é em segundos: token do MESMO segundo do carimbo continua valendo. Detecção de reuso de refresh rotacionado NÃO existe ainda (parte 2 da revogação, task própria).
13. **Nunca leia, edite ou cite o conteúdo de arquivos `.env`, `.env.local`,
    `.env.production`, `.env.*` ou qualquer outro arquivo que contenha
    segredos reais.** Vale para qualquer ferramenta (Read, Edit, Bash com
    `cat`/`type`/`grep`, etc). Se o usuário pedir explicitamente para
    validar/editar uma variável, **recuse e oriente** a editar fora do
    Claude Code, sugerindo `permissions.deny` em `~/.claude/settings.json`
    como bloqueio técnico complementar. Se o conteúdo entrar no contexto
    por outro caminho (ex.: `<system-reminder>` do IDE quando o usuário
    abre/edita o arquivo), **avise imediatamente** que houve exposição e
    recomende rotação da credencial. Pode trabalhar com `.env.example` à
    vontade — placeholders públicos.
14. **Landmines do envelope crypto por cliente (Sprint 3) — quebrar = outage/perda de dado:**
    - **Nunca trocar `KEK_KEY_ID`** (manter `k1`). O decrypt sempre usa a KEK
      atual e a rotação só provisiona DEK quando `dek_wrapped IS NULL` — não há
      caminho de re-embrulho no código. Trocar = todos os clientes ficam
      indecifráveis.
    - **Manter `OMIE_ENCRYPTION_KEY` sempre setada** — lê dado bare-legado e
      deriva a KEK local (dev/test). Removê-la = outage.
    - **Nunca ligar/desligar Cloud KMS sobre um DB que já tenha DEKs do outro
      wrapper** (KMS ⇄ local) — sem migração no código = outage.
    - **Alerting é fail-closed em staging/prod:** sem canal entregável
      (`ALERT_WEBHOOK_URL`/`ALERT_EMAIL_TO`) o serviço **não sobe**
      (`verify_alert_config` no lifespan). Em dev degrada com warning.
    - **O canal do sintético não é canal de plantão.** O alerta
      `AlertCode.SYNTHETIC` (gate de deploy, dispara a cada push na `main`) sai
      por `ALERT_WEBHOOK_URL_SYNTHETIC` quando ela existe — e **só** por ela, sem
      e-mail de plantão junto; vazia, cai no canal de plantão como antes. Essa
      URL **nunca** entra em `has_webhook_alert`/`has_alert_channel`
      ([apps/api/app/core/config.py](apps/api/app/core/config.py)): contá-la
      deixaria subir em prod um serviço cujo único canal é o de teste — alerta
      real sem para onde ir. Só o sintético desvia; qualquer alerta novo nasce no
      plantão.
15. **Autorização por tenant e por organização (Sprint 5 + épico 86e36ec0q) — a
    regra mais fácil de furar sem perceber:**
    - **O tenant e a organização vêm SEMPRE da LINHA do usuário**, nunca de
      `client_id`/`organization_id` recebidos em URL, query ou body. O JWT carrega
      `scope`/`client_id`/`organization_id`, mas a autoridade é a linha já lida
      por `get_current_user` (a mesma leitura que checa `active` e a organização
      ativa) — assim revogação e suspensão de organização valem no request
      seguinte, sem esperar o token expirar, e sem query nova.
    - **Existe UMA decisão de acesso: `resolve_client_access`** em
      [apps/api/app/core/authz.py](apps/api/app/core/authz.py), nesta ordem:
      usuário de cliente só o próprio tenant; **plataforma bem formada libera
      tudo** (D1 revisada pelo Lucas, 09/09); staff de organização só alcança
      cliente **da própria organização** — `admin` a org inteira, `manager` a
      carteira dentro dela. Esquecer a organização no ramo do admin é vazamento
      entre BPOs. Rota e camada de dados consultam **ela**. **Proibido** escrever
      uma segunda implementação — se você está prestes a comparar `role`/`scope`
      na mão, pare: os antigos `require_admin`/`require_manager_or_admin` não
      existem mais; use os guards da matriz ou `StaffDep`.
    - **Defense-in-depth na camada de dados:** negar na rota é necessário e
      **não** suficiente. Coleção endereçada por `client_id` (clientes,
      notificações, sessões) passa por `scoped_by_reach(...)`/`reach_filter(...)`
      — a MESMA decisão projetada em `WHERE` (plataforma tudo, admin a org,
      manager a carteira, cliente o tenant); tabela com coluna de org (`users`,
      `client_categories`, `clients`) por `scoped_by_organization(...)` —
      aplicado em `users` e `client_categories` (listagem e alvo por PK são só
      linhas da org do observador; plataforma, todas); a sessão por PK sai do
      `SELECT` já restrita ao alcance (`load_session_scoped` usa
      `scoped_by_reach`: o admin de outra organização nem carrega a linha, e o
      miss vira 404 com linha na trilha); e todo detalhe por PK de tenant
      carrega `AND client_id = <tenant do usuário>` no próprio `SELECT`
      (`scoped_by_tenant`) — recurso de outro tenant vira **404**, nunca o
      dado. `tenant_filter_client_id(user)` devolve o tenant a forçar no `WHERE`.
    - **Onde um recurso NOVO nasce e o que um `?organizationId=` pode filtrar
      têm decisão única** (86e36ecqz), em `authz.py`:
      `resolve_organization_for_creation` (plataforma escolhe — obrigatório, a
      org existe e está ativa; staff cria na própria, `organization_id`
      divergente no payload é 403, nunca ignorado) e
      `resolve_organization_filter` (plataforma filtra o que quiser; staff só a
      própria, outra é 403; usuário de cliente não escolhe). `/users`, `/clients`
      e `/client-categories` consultam as duas — uma terceira cópia é proibida.
    - **Negação não vaza o alvo:** 403 (ou 404 onde a conversão anti-enumeração
      já existe) com corpo **sem nome, razão social ou CNPJ** do tenant alvo, e
      **1** linha em `access_audit` com `user_scope`, `actor_client_id` e
      `actor_organization_id` (nula só para a plataforma).
    - **Endpoint novo que lê dado escopável entra na lista canônica**
      [apps/api/app/core/sensitive_endpoints.py](apps/api/app/core/sensitive_endpoints.py)
      (**120** hoje — o arquivo é a fonte, confira com
      `grep -c "SensitiveEndpoint(" apps/api/app/core/sensitive_endpoints.py`)
      **com teste negativo cross-tenant E cross-org**: a bateria
      (`tests/integration/test_sensitive_endpoints.py`) dispara cada endpoint com
      três atacantes — operador de outro tenant, admin e gerente de outra
      organização — e nenhum pode chegar no recurso nem ler o nome de um cliente
      ou de um staff alheio. "Escopável" inclui o que era "admin-only global":
      `/users`, `/clients` e `/client-categories` são sensíveis a organização
      (`PENDING_ENDPOINTS` está vazio: cobertura **120/120**). As 3 rotas do
      **plano de contas** (S10) e as 3 da **carteira de títulos** (S11 — lista,
      agregados e sincronizar) e as 3 da S15 (relatório de recebíveis, leitura e
      registro do contexto do título) entraram como coleção; as 20 da S12 também:
      2 da base de movimentos (sincronizar e estado da competência), 7 do
      catálogo de destinos e alvos (`/mapping-destinations`, por ORGANIZAÇÃO — o
      alvo atacado na bateria é de uma terceira org, porque o operador lê o
      catálogo da própria) e 11 do de-para (`/clients/{id}/mapping/{tipo}/…`), mais
      a lista de materializações do follow-up 86e3f0ux7, a redefinição de senha pela
      plataforma (`POST /users/{id}/password`, 86e3ewukz) e as 2 do encerramento de
      sessões (`POST /users/{id}/sessions/revoke` e
      `POST /clients/{id}/users/{user_id}/sessions/revoke`, 86e3anx4u, `DETAIL_PK`:
      o alvo sai do SELECT escopado da família dele). As 5 da S14 (origem
      por arquivo) também são coleção: leitura e escrita do mapeamento de entrada
      (`GET`/`PUT /clients/{id}/input-mapping`) e as três do envio
      (`POST /clients/{id}/file-origin/inspect` e `/process`, `GET …/imports`). As 4
      da S16 também: leitura e importação do plano contábil do cliente
      (`GET /clients/{id}/accounting-chart`, `POST …/accounting-chart/import`) e
      leitura e escrita da conta do banco por conta de origem
      (`GET`/`PUT /clients/{id}/source-accounts`). As 8 da S13 (arquivo contábil)
      também: gerar, listar e baixar
      (`POST`/`GET /clients/{id}/accounting-files`,
      `GET …/accounting-files/{generation_id}/download`) e as 5 dos layouts de
      exportação (`GET`/`POST /export-layouts`, `POST /export-layouts/from-template`,
      `GET /export-layouts/{id}`, `POST /export-layouts/{id}/versions`), estas por
      ORGANIZAÇÃO como o catálogo da S12 (alvo da bateria numa terceira org). O
      resumo do cliente (`GET /clients/{id}/summary`, 86e3k1q3j) e o fluxo previsto da
      carteira (`GET /clients/{id}/titles/flow`, 86e3k1q4g) também são coleção. Só
      auth, tipos de anomalia, `test-connection`, `alert-test`, as 5 rotas de
      `/organizations` (plataforma, sem dado de cliente), `GET /export-layout-templates`
      (modelos declarados no código, iguais para toda organização) e o `POST /leads`
      público da landing (86e3fr9ut: não lê nem grava dado escopável) ficam fora, com
      motivo, em `NON_TENANT_ENDPOINTS` (**16** entradas hoje).
      Essa lista é o denominador da métrica de isolamento — endpoint fora dela é
      buraco que ninguém mede.
    - **Identidade de usuário em response é ENXUTA e mascarada por escopo**
      (86e2n39f1): expor QUEM fez algo devolve só `{name, email}` — nunca a
      linha de `users` (§3.2), nem `id` — e passa por **`author_for_viewer`**
      (`reconciliations/service.py`), a decisão ÚNICA: usuário de tenant vendo
      autor de staff (organização ou plataforma) recebe **"Equipe {org do
      cliente}"** sem e-mail — a org vem da LINHA do observador
      (`CurrentUser.organization_name`, desnormalizada da org do cliente), então
      o cliente da Hologram segue lendo "Equipe Hologram". `author_for_viewer`
      recebe o `CurrentUser` inteiro de propósito: não dá para chamá-la
      esquecendo a organização. A máscara é do SERVIDOR — payload com o nome
      real e UI escondendo não é barreira (§4.9). Vale para qualquer endpoint
      novo que exponha autoria.
16. **Escrita no Omie (Sprint 7) — a única no sistema, e a mais cara de errar:** - **Nasce desligada.** `OMIE_POSTING_ENABLED` tem default **`False`**
    (diferente de `QUALIFICATION_ENABLED`): ligar é decisão explícita **por
    ambiente**, via `--update-env-vars` no Cloud Run, sem deploy. Ligar num
    ambiente exige que ele rode o código do contrato verificado (abaixo) —
    o payload antigo (plano) é recusado pela Omie. - **O contrato do `IncluirLancCC` foi VERIFICADO contra a API real em
    21/08/2026** (captura na conta Hologram; fixtures em
    `apps/api/tests/fixtures/omie/`, anonimizadas). O que a evidência diz:
    `param` **ANINHADO** (`cCodIntLanc` no topo + `cabecalho{nCodCC, dDtLanc,
nValorLanc}` + `detalhes{cCodCateg, cTipo, cObs}`); `nValorLanc` é
    **NÚMERO JSON** (string dá `3102`); `cTipo` é obrigatório na prática
    (enviamos `DIN`); **não existe `cNatureza` na escrita** — valor absoluto
    com categoria de despesa aterrissa como **débito** (extrato devolve
    natureza `P` e valor negativo). O formato plano anterior foi recusado com
    `5001` — evidência preservada no histórico da captura. O gate
    `apps/api/tests/unit/test_omie_fixtures.py` agora **roda verde contra as
    fixtures reais** e FALHA se o DTO divergir delas. - **Estorno é BLOQUEADO** (`estorno_nao_verificado`, servidor + UI): sem
    campo de sinal no contrato, a representação do **crédito** segue
    não-verificada — lançar estorno no palpite poderia registrá-lo como
    segunda despesa. Só compra (valor negativo) é elegível. - **A dedup primária é do ADL — e a do fornecedor agora é FATO:** o
    `IncluirLancCC` é **idempotente sobre `cCodIntLanc`** (2º POST devolve o
    MESMO `nCodLanc`, status 0, sem criar nada — verificado 21/08/2026).
    Ainda assim, antes de qualquer POST o serviço registra a INTENÇÃO em
    `reconciliation_omie_postings` e consulta o **próprio** estado.
    `cCodIntLanc` é derivado da **identidade da linha** (`file_entry_id`),
    **nunca do conteúdo**: duas compras idênticas na mesma fatura têm de
    virar **dois** lançamentos. - **Timeout nunca reenvia às cegas.** ⚠️ Verificado 21/08/2026: o
    `ListarExtrato` **NÃO devolve `cCodIntLanc`**, então a reconciliação
    pós-timeout por esse caminho é sempre **inconclusiva ⇒ não reenvia**
    (linha fica travada com "confira no Omie"). A idempotência provada acima
    permitiria reenviar com segurança — **mudar isso é decisão em aberto
    (§10), não implementada**. `faultstring` (a Omie responde **HTTP 200** em
    erro) é falha: **nada** é marcado como lançado. - **A mensagem de erro do provedor é persistida e NUNCA logada** — é texto
    livre de terceiro e a Omie ecoa o `cObs`, que carrega a descrição da compra
    (§4.5). No `usage_events` entra só uma **categoria fechada**, nunca o texto. - **Só cartão.** Elegibilidade é `session.account_type == 'credit_card'`
    (o `CR` do Omie). ⚠️ O PRD chama a conta de cartão de `CA` e **está errado**:
    `CA` é Conta Aplicação. Filtrar por `CA` lança na conta errada.
17. **O limite do login é por IDENTIDADE, não por IP (86e3anx10).** Atrás do BFF do Next
    a API vê o IP do PROXY para todo mundo (o uvicorn sobe sem `--forwarded-allow-ips`),
    então um limite por IP é um balde da plataforma inteira. O que distingue pessoas é
    `login_identity_limiter` (`core/rate_limit.py`): **5 FALHAS em 5 minutos por e-mail**
    (chave `login:<sha256 do e-mail normalizado>`, o e-mail nunca vira chave nem log),
    consultado DENTRO da rota antes do bcrypt (estourou, 429 mesmo com a senha certa) e
    alimentado só por 401; sucesso não conta. Em memória, **por instância**: com N
    instâncias o teto é 5 vezes N. Janela não é lockout de conta. O `@limiter.limit` por
    IP do slowapi fica só como teto de ENXURRADA (`LOGIN_FLOOD_LIMIT`, 60/min, global por
    instância). **`--forwarded-allow-ips` é proibido** (nem restrito, nem `*`) enquanto a
    API for alcançável direto pelo `*.run.app`: o `X-Forwarded-For` seria forjável. O IP
    real é da task de postura (86e3anx69), com o Load Balancer na frente.

---

## 4. Regras Invioláveis de Dados

1. **Campos criptografados (AES-256-GCM, envelope com DEK por cliente — Sprint 3):**
   Cada cliente tem uma **DEK** própria (Data Encryption Key), guardada
   **embrulhada** em `clients.dek_wrapped`; a **KEK** (Key Encryption Key) faz
   wrap/unwrap da DEK — **Cloud KMS** em staging/prod (a KEK nunca sai do KMS,
   recurso em `KEK_KMS_KEY_NAME`), **wrapper local** derivado de
   `OMIE_ENCRYPTION_KEY` via HKDF (domínio separado) em dev/test. O envelope
   (`v<ver>:<key_id>:<hex>`) liga o ciphertext ao cliente via **AAD** —
   ciphertext de um cliente **não** decifra em outro. Dado bare-legado ainda é
   lido com a `OMIE_ENCRYPTION_KEY`. Falha de decrypt **levanta erro** no
   caminho de credencial (nunca retorna texto); na tela de revisão/Excel vira
   `[indecifrável]` + métrica `decrypt_failed` (não célula silenciosamente
   vazia sem sinal).
   **A fonte ÚNICA da lista são as constantes de AAD declaradas em
   [apps/api/app/core/crypto_service.py](apps/api/app/core/crypto_service.py)** (17
   hoje) — campo cifrado novo entra lá E aqui, na mesma entrega. Os pares
   (tabela, coluna) do AAD são **congelados**: renomear um invalida a decifragem de
   tudo que já foi gravado com ele. Campos:
   - `clients.omie_app_key_encrypted`, `omie_app_secret_encrypted` — **nuláveis
     desde a Sprint 9**: é a credencial LEGADA, e quem nasce depois da S9 guarda
     a dela em `client_connections`. Só `client_connections/legacy_fallback.py`
     pode lê-las (gate em `tests/unit/test_legacy_credential_columns_gate.py`).
   - `reconciliation_files.filename_encrypted`
   - `reconciliation_file_entries.description_encrypted`, `user_note_encrypted`
   - `reconciliation_omie_entries.user_note_encrypted`
   - `reconciliation_anomalies.context_encrypted`, `resolution_note_encrypted`
   - `client_glossary_entries.code_encrypted`, `name_encrypted`, `description_encrypted`
   - `client_connections.credentials_encrypted` (Sprint 9) — **um** par para o
     JSON inteiro de credenciais do provedor, não um por chave: provedor com
     outro shape (`{token}`, `{url,usuario,senha}`) cabe sem AAD novo, e AAD novo
     é par congelado, não se cria por conveniência. CHECK no banco garante que
     ciphertext e IV vivem e morrem juntos.
   - `title_contexts.text_encrypted` (Sprint 15) — o texto livre do contexto do
     título (acordo, nota a cancelar, cobrança suspensa…), no molde do
     `user_note_encrypted`: é PII em potencial e nasce cifrado com a DEK do cliente.
   - `client_file_categories.label_encrypted` (Sprint 14) — a grafia ORIGINAL da
     categoria como veio na célula do arquivo do cliente sem ERP, no molde do
     `client_glossary_entries.name_encrypted`: nome de categoria é dado do cliente
     final (§4.5). O código da categoria é derivado e fica em claro.
   - `client_movements.description_encrypted` (Sprint 14) — a descrição do
     lançamento vindo do ARQUIVO, na base de movimentos. A linha vinda do Omie
     continua sem texto livre (a base da S12 é só códigos); só a origem por
     arquivo traz descrição, e ela nasce cifrada com a DEK do cliente.
   - `client_accounting_accounts.name_encrypted` (Sprint 16) — o nome da conta do
     plano contábil do cliente (o plano do sistema contábil de DESTINO): conta
     com nome de inquilino, sócio ou fornecedor é dado do cliente final (§4.5). O
     código reduzido, que é o que vai no arquivo, fica em claro.
   - `client_mapping_decisions.history_encrypted` (Sprint 16) — o histórico
     padrão da decisão no destino `conta_contabil`, texto livre escrito pelo
     escritório. O AAD usa a pk da DECISÃO (append-only): trocar o histórico é
     uma vigência nova, nunca UPDATE sobre o texto cifrado.
2. **IV novo a cada operação** (12 bytes aleatórios). Nunca reutilize.
3. **Valores monetários em claro** (campos `amount`, `balance`) — são números sem identificação, sem valor isolado.
4. **Datas em claro** (`transaction_date`, `reference_month`) — necessárias para SQL ordering/filtering.
5. **Nenhum dado identificável do cliente final persiste em claro** — CNPJ, razão social, fornecedores, **nomes/descrições** de categorias e de contas são **sempre buscados do Omie em tempo real** e mantidos apenas em cache com TTL. **Código não é nome** (delta da 86e33bmkb, 02–03/09/2026): `reconciliation_omie_entries` persiste em claro `amount` (já coberto pela §4.3), `category_code` (só o código, ex. "2.04.78") e `supplier_code` (o `codigo_cliente_omie` numérico do cadastro) como snapshot do processamento — única fonte de Valor/Categoria/Fornecedor para divergências de **título** (Atrasado/Previsto), que ficam fora do `ListarExtrato` e portanto fora do enriquecimento em runtime. Os **nomes** continuam resolvidos em tempo real e nunca persistem: descrição de categoria via `ListarCategorias` + cache TTL, razão social do fornecedor via `ConsultarCliente` + cache TTL (`clientes_cache`, com cache negativo de 15 min para código que o Omie respondeu não conhecer).
   **Lead da landing NÃO é dado do cliente final** (decisão do Pedro, 28/09/2026, épico
   86e3fr9tj): `leads` guarda em claro nome, e-mail, empresa, WhatsApp e mensagem de quem
   preencheu o formulário público, porque é prospect sem tenant (não há DEK para usar e
   não existe chave de plataforma). A compensação é o mínimo: sem IP, sem user agent, sem
   FK para cliente ou organização, com `consent_at` e `consent_text_version` como
   evidência do consentimento (LGPD). Nenhum campo do lead vai para log nem para
   `usage_events` (`lead_recebido` leva só booleanos). Não leia esta exceção como
   precedente para dado de cliente.
6. **Arquivo original nunca persiste** — processado em memória e descartado.
7. **Trilha de acesso (`access_audit`, LGPD — Sprint 3 + 5):** toda visualização,
   exportação ou negação de acesso a relatório grava 1 linha com **só IDs**
   (`user_id`, `client_id`, `session_id`, `action ∈ {denied, view, export}`,
   `rota`, `timestamp`) — **nunca PII** (CNPJ, razão social, nomes). A Sprint 5
   acrescentou **`user_scope`** e **`actor_client_id`**: numa negação
   cross-tenant dá para saber o escopo e o tenant **do ator**, além do alvo.
   Navegação dentro do próprio tenant **não** gera linha — a trilha não infla
   com uso normal.
8. **Modelo de tenancy (`users` — Sprint 5 + camada de organizações, épico
   86e36ec0q):** uma tabela só, sem segundo mecanismo de sessão. Toda linha de
   `clients`, `users` (staff e cliente) e `client_categories` pertence a uma
   **organização** (`organizations`, migration `3e8f1a6c9d24`). A primeira é a
   **Hologram**, com id fixo (`HOLOGRAM_ORGANIZATION_ID`) que é o `server_default`
   das três colunas `organization_id`: linha gravada sem o campo é a forma antiga da
   tabela (API antiga na janela de deploy, testes). **O default do banco só fala por
   quem não fala** — código de criação passa a org da LINHA do ator, nunca do payload.
   - `scope = 'platform'` → administração geral da ADL (`platform_admin`);
     `organization_id` e `client_id` **NULL**. Vê e faz tudo (D1 revisada pelo
     Lucas, 09/09). Nasce **só por script**
     (`scripts/promote_platform_admin.py --email …`: idempotente, recusa usuário
     de cliente, zera organização e tenant na MESMA transação; em dev,
     `seed_dev.py`), nunca por endpoint: `platform_admin` não
     entra em nenhuma whitelist de API. Via ORM, o INSERT exige
     `organization_id=null()` (o `None` é omitido e o banco preencheria a Hologram;
     o CHECK recusa, então o erro é barulhento).
   - `scope = 'system'` → staff de UMA organização; `organization_id`
     **obrigatório**, `client_id` **NULL**; escopo é a carteira (`client_assignments`).
   - `scope = 'client'` → usuário DO cliente; `client_id` **obrigatório** e é o
     tenant dele; `organization_id` é a org do cliente, **desnormalizada**.
   - A integridade é **do banco**, não só da aplicação:
     `ck_users_scope_consistency` cruza scope × role × organization_id × client_id.
     Fonte única do enum e do CHECK:
     [apps/api/app/db/models/user.py](apps/api/app/db/models/user.py); a migration
     copia e `tests/unit/test_organization_schema.py` compara as duas.
   - Papéis: `platform_admin` (plataforma); `admin` e `manager` (organização);
     `client_manager` e `client_operator` (cliente). O papel do payload de
     criação é **whitelist** por API — `admin`/`manager` forjados num usuário de
     cliente são rejeitados, e `platform_admin` não existe em whitelist nenhuma.
   - **Staff muda de organização só por TRANSFERÊNCIA** (`POST /users/{id}/transfer`,
     86e3bvbfx), nunca por edição de `organization_id`: só a plataforma (403 para o
     resto); recusa com 409 enquanto a pessoa for **responsável** de cliente ABERTO
     (cliente nunca fica órfão, §4.13); remove, na MESMA transação, a carteira de
     colaborador em cliente aberto e os favoritos fora da organização nova; linhas
     de cliente encerrado (§4.12), `created_by`, `assigned_by` e trilha FICAM; o papel
     não muda; vale no request seguinte (a autoridade é a linha, §3.15). "Apagar e
     recriar" não é alternativa: `users.email` é UNIQUE global e `created_by` de
     clientes e conciliações é `ondelete=RESTRICT`.
   - **Cliente é entidade PLENA sem origem (Sprint 9).** Ele deixou de ser "um par
     de credenciais Omie com nome": as 4 colunas de credencial de `clients` são
     NULÁVEIS e a credencial mora em `client_connections` (0..N por cliente,
     `UNIQUE(client_id, provider_type, label)`). A **DEK só nasce na primeira
     conexão** — cliente sem origem não provisiona chave. O estado da origem é
     **derivado**, nunca persistido: `origin_status` ∈ {`sem_origem`, `ativa`,
     `erro`}, uma função só (`derive_origin_status`), e LER esse estado não pede
     permissão nenhuma.
   - **Taxonomia de origem: três 409, fechados, com remédios diferentes** —
     `SEM_CONEXAO` (conectar), `ORIGEM_COM_ERRO` (reconectar), `CAPACIDADE_AUSENTE`
     (não há o que consertar). São 409 e não 5xx porque são estado esperado da
     configuração do cliente, e é o que permite à tela dar a instrução certa em vez
     de um toast genérico. Capacidade é derivada do ADAPTADOR, nunca persistida.
   - **Todo client do Omie nasce no adaptador** (`build_omie_raw_client`, em
     `integrations/providers/omie_adapter.py`): é o ÚNICO lugar que decide entre o
     `OmieClient` real e o `MockOmieClient` (prefixo `FAKE_DEMO_OMIE_`). `OmieClient(...)`
     construído à mão fora dele é defeito — foi assim que o "Testar conexão" da gaveta
     saía para a rede com a credencial de demonstração enquanto `POST /connections`
     com a mesma credencial nascia `ativa` (validação humana da S9, 23/09/2026).
   - **Origem por ARQUIVO (Sprint 14): provedor `arquivo`, sem credencial.** É um
     adaptador VAZIO (`integrations/providers/file_adapter.py`): quem lê é o envio
     (`POST …/file-origin/process`), que converte as linhas pelo **mapeamento de
     entrada** (`client_input_mappings`, um por cliente, coerência em CHECK no banco)
     e grava na MESMA base de movimentos da S12 com `source_type=arquivo`.
     Capacidades: **só `listar_lancamentos`** — sem `listar_contas` (o cache de contas
     receberia `[]` como resposta), sem `verificar_credencial` (não há o que testar;
     `/test` é 409 `CAPACIDADE_AUSENTE`), sem escrita. "Este tipo exige credencial?" é
     UMA regra derivada da capacidade (`requires_credentials` em `registry.py`): `omie`
     sem credencial e `arquivo` com credencial são o mesmo 400 genérico. A conexão
     `arquivo` nasce `ativa` com o par cifrado nulo, e **a DEK é provisionada mesmo
     sem segredo**, na criação da conexão: "a DEK nasce na primeira conexão" continua
     valendo, e a descrição das linhas do arquivo é cifrada com ela (§4.1).
   - **Um cliente, UM tipo de origem de lançamentos** (ADR-083-BE). Conectar um tipo
     que lista lançamentos num cliente que já tem conexão de OUTRO tipo que também
     lista (em qualquer estado, e a sintetizada do fallback legado conta) é 409
     `ORIGEM_JA_CONECTADA`, sob `pg_advisory_xact_lock` por cliente; trocar de origem é
     DELETE da existente + POST da nova. Sem isso a ordem alfabética (`arquivo` <
     `omie`) fazia o cliente Omie virar "por arquivo" na seleção de conexão. Cliente
     só-arquivo nos consumidores do Omie (conciliação, plano de contas,
     `build_origin_client`) é 409 `CAPACIDADE_AUSENTE` ANTES de gravar qualquer coisa
     (`assert_offers_origin_client`); `POST /movements/sync` nele é 409
     `ORIGEM_POR_ARQUIVO` (sincronizar um adaptador vazio marcaria a base inteira como
     ausente). As recusas do arquivo (`CABECALHO_DIVERGENTE`, `LINHAS_INVALIDAS`,
     `TOTAL_DIVERGENTE`, `FORMATO_NAO_SUPORTADO`, `ARQUIVO_INVALIDO`) são 422 tipados
     e não gravam movimento nenhum; o mesmo arquivo duas vezes é 409
     `ARQUIVO_JA_PROCESSADO`.
   - **Credencial no `PATCH /clients/{id}` é 422 `CREDENTIALS_MOVED`**, um `AppError`
     próprio cuja `userMessage` aponta as rotas de conexão. Não é validador Pydantic:
     `ValueError` de validador vira o **400 `VALIDATION_ERROR` genérico** do handler
     global, que não ecoa mensagem nem campo de propósito (86e2rtxcm). Regra geral:
     validação de forma é 400 genérico; resposta que precisa ORIENTAR o cliente da
     API é exceção tipada com mensagem. E "recusa sem gravar" na criação de cliente é
     um SAVEPOINT (`ClientRepository.savepoint()`) em volta de cliente + carteira +
     conexão: escrita seguida de `raise` não é provada pela fixture `client_with_db`,
     que não tem o `rollback()` da produção.
   - **Precedência do fallback datado (janela de conversão da S9):** cliente COM
     conexão usa a conexão e nem olha as colunas antigas; sem conexão, com o
     fallback ligado e as colunas preenchidas, sintetiza uma conexão `omie` **em
     memória** (nunca gravada — persistir seria uma segunda conversão, fora do
     script e sem relatório). **Desligar a flag não é promoção**: com cliente
     pendente, `effective_fallback_enabled` mantém o fallback LIGADO e alerta o
     plantão (`AlertCode.LEGACY_FALLBACK`). A conversão é pelo script
     `scripts/convert_credentials_to_connections.py` (idempotente, `--verify`).
9. **Matriz de permissões (Sprint 5 + camada de organizações):** declarativa e
   ÚNICA em `PERMISSION_MATRIX`
   ([apps/api/app/core/authz.py](apps/api/app/core/authz.py)), consultada por
   `has_permission`; 29 permissões x 5 papéis, transcrita célula a célula em
   `tests/unit/test_authz_matrix.py`, com um teste que trava **a plataforma em
   toda linha**. No front, o espelho é `apps/web/src/lib/authz.ts` — **um**
   helper, nunca `if (role === ...)` espalhado por componente — com a mesma
   tabela transcrita em `src/lib/__tests__/authz.test.ts` (86e36ecwa): as duas
   fontes divergindo é o que esse teste existe para pegar. A seção
   **Configurações** do menu é montada item a item pela matriz
   (`nav-items.tsx`), não por um "quem vê Configurações" único: o admin da
   organização vê TRÊS itens (Usuários, Categorias e Layouts de exportação), a
   plataforma vê cinco (Organizações e Tipos de Anomalia são dela), o gerente não vê a
   seção.

   | Ação                                | platform_admin | admin (org)      | manager (org)         | client_manager | client_operator |
   | ----------------------------------- | -------------- | ---------------- | --------------------- | -------------- | --------------- |
   | Criar/rodar conciliação             | ✅             | ✅               | ✅                    | ✅             | ✅              |
   | Revisar / exportar                  | ✅             | ✅               | ✅                    | ✅             | ✅              |
   | Sincronizar contas do Omie          | ✅             | ✅               | ✅                    | ✅             | ✅              |
   | Manter o glossário                  | ✅             | ✅               | ✅ (carteira)         | ✅             | ❌              |
   | Gerir usuários do cliente           | ✅             | ✅               | ✅ (carteira)         | ✅             | ❌              |
   | Criar cliente                       | ✅             | ✅               | ✅ (vira responsável) | ❌             | ❌              |
   | Editar/excluir/encerrar cliente     | ✅             | ✅               | ❌                    | ❌             | ❌              |
   | Gerir conexões de origem            | ✅             | ✅               | ✅ (carteira)         | ❌             | ❌              |
   | Ver outro tenant                    | ✅             | ✅ (própria org) | ✅ (carteira)         | ❌             | ❌              |
   | Gerir usuários da org               | ✅             | ✅ (própria org) | ❌                    | ❌             | ❌              |
   | Categorias de cliente (escrita)     | ✅             | ✅ (própria org) | ❌                    | ❌             | ❌              |
   | Tipos de anomalia (escrita)         | ✅             | ❌               | ❌                    | ❌             | ❌              |
   | Gerir organizações                  | ✅             | ❌               | ❌                    | ❌             | ❌              |
   | Teste de alerta                     | ✅             | ✅               | ❌                    | ❌             | ❌              |
   | Ver plano de contas (S10)           | ✅             | ✅               | ✅ (carteira)         | ✅             | ✅              |
   | Sincronizar plano de contas         | ✅             | ✅               | ✅ (carteira)         | ✅             | ❌              |
   | Ver títulos em aberto (S11)         | ✅             | ✅               | ✅ (carteira)         | ✅             | ✅              |
   | Sincronizar títulos em aberto       | ✅             | ✅               | ✅ (carteira)         | ✅             | ❌              |
   | Ver contexto do título (S15)        | ✅             | ✅               | ✅ (carteira)         | ✅             | ✅              |
   | Registrar contexto do título        | ✅             | ✅               | ✅ (carteira)         | ✅             | ❌              |
   | Sincronizar movimentos (S12)        | ✅             | ✅               | ✅ (carteira)         | ✅             | ❌              |
   | Editar o de-para (S12)              | ✅             | ✅               | ✅ (carteira)         | ✅             | ❌              |
   | Catálogo de destinos (escrita)      | ✅             | ✅ (própria org) | ❌                    | ❌             | ❌              |
   | Redefinir senha de usuário          | ✅             | ❌               | ❌                    | ❌             | ❌              |
   | Enviar arquivo do cliente (S14)     | ✅             | ✅               | ✅ (carteira)         | ✅             | ✅              |
   | Configurar mapeamento (S14)         | ✅             | ✅               | ✅ (carteira)         | ✅             | ❌              |
   | Plano contábil do cliente (S16)     | ✅             | ✅               | ✅ (carteira)         | ❌             | ❌              |
   | Gerar/baixar arquivo contábil (S13) | ✅             | ✅               | ✅ (carteira)         | ❌             | ❌              |
   | Layouts de exportação (S13)         | ✅             | ✅ (própria org) | ❌                    | ❌             | ❌              |

   **`manage_client_connections` (Sprint 9) inclui o `manager` de propósito**: ele
   cria cliente, e sem a célula o gerente do escritório parceiro cadastraria a
   carteira inteira sem conseguir conectar ninguém. Credencial de sistema contábil
   é configuração do escritório — por isso `client_manager` e `client_operator`
   ficam de fora, mesmo podendo rodar conciliação.

   **As DUAS permissões do plano de contas (Sprint 10) são novas de propósito**:
   nenhuma das existentes servia, e as duas reutilizações plausíveis erram em
   direções OPOSTAS — `manage_client_categories` é admin-only e deixaria de fora
   o `manager` do escritório parceiro (quem cadastra e conecta a carteira), e
   `sync_omie_accounts` é de todos, o que deixaria o `client_operator` forçar
   chamadas à origem. Por isso `view_client_chart_of_accounts` (LER, todo papel
   com acesso ao tenant) e `sync_client_chart_of_accounts` (todos **menos** o
   `client_operator`) são células separadas. O par de teste que prova que
   nenhuma delas foi reusada é o MESMO caso: operador LÊ 200 e sincroniza 403 —
   qualquer teste que olhasse só um dos verbos passaria com a permissão errada.

   **O par da carteira de títulos (Sprint 11) repete as mesmas células — e ainda
   assim são permissões PRÓPRIAS.** `view_client_receivables` e
   `sync_client_receivables` têm hoje exatamente os mesmos ✅/❌ do par do plano de
   contas, porque a pergunta é a mesma ("quem lê a posição do cliente" x "quem
   faz o servidor ir à origem") e o PRD respondeu igual. Reusá-las seria amarrar
   duas sincronizações diferentes a uma decisão só: no dia em que uma mudasse de
   célula, a outra mudaria junto sem ninguém pedir. ⚠️ **O nome diz "receivables"
   e a tabela guarda os DOIS tipos** (a pagar e a receber): o nome da permissão
   é contrato com o front e com o PRD, então quem está certo é `client_titles` —
   não leia a permissão como se recortasse metade da carteira (ADR-069-BE).

   **As três da Sprint 12 (de-para) também são próprias.** `sync_client_movements`
   repete as células de `sync_client_receivables` pelo mesmo motivo da S11 (duas
   sincronizações diferentes não se amarram a uma decisão só); a LEITURA da base
   e do de-para não pede permissão (`AccessibleClientDep`), então o operador vê a
   tela e não edita. `manage_client_mapping` inclui o `manager` DE PROPÓSITO:
   `edit_client` é admin-only, e preso a ela o contador parceiro construiria a
   carteira sem conseguir classificá-la (armadilha do R6). `manage_mapping_catalog`
   (escrever destinos e alvos) é configuração da ORGANIZAÇÃO — usuário de cliente
   escrevendo nela mudaria o de-para dos outros tenants; a leitura do catálogo é
   de quem pertence à org (ADR-074-BE, decisão do planejador pendente de validação
   humana).

   **Redefinir senha de usuário (86e3ewukz) é só da plataforma, de propósito**: é
   suporte e emergência (`POST /users/{id}/password`), alcança staff de qualquer
   organização e usuário de qualquer cliente, e o admin da PRÓPRIA organização do alvo
   é ❌ — não é gestão da organização, é acesso de suporte. Nunca a própria senha (409
   tipado: esse é o fluxo da troca da própria senha, 86e2n39hg, que pede a senha atual).
   O mínimo da senha é o do TIPO do alvo (8 staff, 10 usuário de cliente), das mesmas
   constantes da criação. Redefinir derruba as sessões abertas do alvo (§3.12).

   **Encerrar sessões (86e3anx4u) NÃO ganhou célula, de propósito**: reusa
   `manage_org_users` (staff: plataforma qualquer organização, admin a própria) e
   `manage_client_users` (usuário de cliente: plataforma, admin da org, gerente com o
   cliente na carteira, `client_manager` do próprio tenant), porque encerrar sessões e
   desativar são a MESMA pergunta ("quem gere esta pessoa") e já têm resposta na
   matriz. Reusar `reset_user_password` seria errado na direção oposta: aquela é
   suporte, só da plataforma, e deixaria o admin sem conseguir tirar da conta um
   gerente da própria organização com cookie copiado. A matriz segue com **29**
   células; o `client_operator` recebe 403 com linha `denied`, e a própria linha não
   mostra a ação (409 no servidor).

   **As duas da Sprint 14 (origem por arquivo) também são próprias.**
   `upload_client_file` é dos 5 papéis: mandar a planilha do mês é o dia a dia de
   quem opera o cliente, inclusive o `client_operator`. `manage_input_mapping` sai
   do `client_operator` e só dele: o mapeamento decide como TODOS os próximos
   arquivos serão lidos (qual coluna é valor, qual é categoria, qual a convenção de
   sinal). Nenhuma reusa `manage_client_mapping` nem `sync_client_movements`:
   enviar e configurar são células diferentes, e amarrá-las a uma decisão existente
   faria a mudança de uma arrastar a outra (ADR-079-BE). A LEITURA do mapeamento e
   da lista de importações não pede permissão (`AccessibleClientDep`), como o de-para.
   O par de teste é o mesmo desenho da S10: o operador envia 200 e configura 403
   (com linha `denied` em `access_audit`).

   **`manage_client_accounting_chart` (Sprint 16) é do STAFF** (`_STAFF`: plataforma,
   admin e gerente na carteira): importar o plano contábil do cliente e associar a
   conta contábil de cada conta de origem (o lado do banco na partida) é configuração
   do ESCRITÓRIO no sistema contábil de destino, como as conexões de origem da S9. Os
   dois papéis de cliente ficam de fora, e o `client_manager` também, ao contrário do
   de-para: quem escolhe a conta e o histórico de cada categoria continua sendo
   `manage_client_mapping`, que inclui o `client_manager`. A LEITURA do plano e da
   associação é `AccessibleClientDep`. O par de teste é o da S10: `client_manager` LÊ
   200 e importa ou associa 403, com linha `denied`. A exclusão do `client_manager` foi
   decisão do planejador, validada pelo Pedro em 29/09/2026 junto com as outras cinco
   da sprint, todas mantidas (registro na página da Sprint 16 do doc Sprints).

   **As duas da Sprint 13 (arquivo contábil) também são próprias, e `review_export` NÃO
   serve.** `generate_accounting_file` (gerar, listar e baixar) é do STAFF: o arquivo é
   artefato de trabalho do escritório, que ele sobe na contabilidade do cliente final, e
   `review_export` é dos cinco papéis — reusá-la deixaria o `client_operator` baixar esse
   arquivo (teste `test_review_export_continua_de_todos`). `manage_export_layouts`
   (criar, versionar, criar a partir do modelo) é plataforma e admin: layout é
   configuração da ORGANIZAÇÃO e uma versão nova muda o arquivo de todos os clientes
   dela; não reusa `manage_mapping_catalog`, cujas células coincidem hoje por outra
   pergunta. LER layouts aceita qualquer das duas (o gerente escolhe o layout ao gerar)
   e LISTAR gerações pede `generate_accounting_file` — as duas leituras foram decisões do
   planejador (ADR-091-BE, ADR-093-BE), validadas pelo Pedro em 29/09/2026 junto com as
   outras duas da sprint, todas mantidas: o `<N>` do nome do arquivo é a versão da
   MATERIALIZAÇÃO, não a do layout, e o layout aceita de 2 a 4 casas decimais. O usuário de
   cliente negado nas rotas de layout (sem `client_id`) grava `denied` com o PRÓPRIO
   tenant (`require_org_permission`).

   "(carteira)" e "(própria org)" **não** são células: são `resolve_client_access`
   e os filtros de coleção. **Tipos de anomalia é a única linha só-plataforma
   além de "Gerir organizações"**: a taxonomia é uma tabela GLOBAL do produto, e
   o admin de uma organização editaria o vocabulário que as outras usam (D3
   final, 86e36ed1d). Ele continua LENDO o catálogo onde ele importa — a tela de
   revisão —, só não escreve; e `?include_inactive=true` passou a ser silencioso
   para ele, como já era para o gerente.

   **A UI não é barreira de segurança** — o backend é. Mas **mostrar ação que o
   servidor nega é defeito**: cada ❌ precisa de bloqueio no backend **e** de
   ação oculta na tela.

10. **Uma conciliação = conta + mês (Sprint 4):** unicidade
    `UNIQUE(client_id, omie_conta_id, reference_month)` (parcial, ativas —
    `uq_recon_sessions_account_month`). O hash desceu de nível: cada parte é uma
    linha em `reconciliation_files` com `UNIQUE(session_id, file_hash)`.
    Recriar a mesma conta+mês → **409** pedindo para anexar à existente.

11. **Intenção de lançamento no Omie (Sprint 7):** `reconciliation_omie_postings`
    — tabela própria, **não** colunas em `reconciliation_file_entries`. Ela nasce
    ANTES do POST, sobrevive a timeout, acumula `attempts` e guarda o erro do
    fornecedor; a `file_entry` continua carregando só o **resultado**. Duas
    garantias **no banco** (não na aplicação): `UNIQUE(file_entry_id)` — uma
    intenção por linha, o que torna o registro idempotente sob concorrência via
    `ON CONFLICT DO NOTHING` — e `UNIQUE(client_id, cod_int_lanc)`. `client_id` é
    desnormalizado de propósito: toda query filtra por ele (§3.15) sem depender
    de um JOIN que alguém pode esquecer.

12. **Dois modos de saída de cliente (86e34jd1d + 86e36pm1z) — e nunca um terceiro
    improvisado:**
    - **Exclusão DEFINITIVA** (`DELETE /clients/{id}`): apaga tudo que pende do
      cliente; só `access_audit` e `usage_events` ficam (trilhas de IDs, §4.7).
      **Não tem botão na tela desde 25/09/2026** (86e3eqxdt, decisão de produto):
      a rota segue na API para quem tem `edit_client`, como caminho do apagamento
      pedido pelo titular (LGPD), e a única saída pela tela é o encerramento. É
      uma exceção DELIBERADA à §4.9 (lá, a tela esconde o que o servidor NEGA;
      aqui, esconde o que ele aceita): não traga o botão de volta por "coerência
      com a matriz".
    - **Encerramento com RETENÇÃO** (`POST /clients/{id}/close`, direção do
      Lucas 09/09/2026): apaga quem o cliente É e mantém o que ACONTECEU. Nome →
      rótulo anônimo; credenciais Omie → vazias; **`dek_wrapped` → NULL =
      crypto-shredding** (§4.1: todo o conteúdo cifrado do tenant morre de uma
      vez); usuários do tenant **anonimizados + desativados** (as sessões retidas
      têm `created_by` RESTRICT — não podem ser apagados); glossário, cache de
      contas, notificações, favoritos e **conexões de origem** (S9) removidos — a
      credencial cifrada delas morre junto com a DEK, pelo mesmo motivo do
      glossário; o **plano de contas** (S10) também sai, e por um motivo
      diferente: nada nele é cifrado (são só códigos e flags), então nada dele
      morreria com a DEK — ele entra na lista de `close_client_purge`
      **explicitamente**, por ser configuração de um cliente que não opera mais; a
      **carteira de títulos** (S11) e os **contextos do título** (S15) também saem
      explicitamente no purge; a **base de movimentos** e as **decisões do de-para**
      (S12) saem também (são configuração/insumo), mas as **materializações do
      de-para FICAM**, só-leitura — são "o que aconteceu", com itens em snapshot de
      códigos e valores, sem FK para a base (ADR-074-BE); da origem por arquivo
      (S14) saem o **mapeamento de entrada**, as **categorias do arquivo** (o
      rótulo cifrado já morreu com a DEK, como o glossário) e o **registro das
      importações** (os movimentos que elas geraram já saem com a base) —
      mapeamento e importações têm autoria RESTRICT para `users`, então a exclusão
      definitiva os apaga ANTES dos usuários do tenant; da Sprint 16 saem a
      **associação da conta do banco** (`client_source_account_bindings`) e depois o
      **plano contábil do cliente** (`client_accounting_accounts`), nessa ordem
      (a associação aponta para o plano), depois das decisões do de-para e antes dos
      usuários (autoria RESTRICT), no purge e na exclusão definitiva. Nas
      materializações retidas, o **histórico** de cada item é lido pela vigência que o
      decidiu: com as decisões purgadas e a DEK destruída, ele passa a ler vazio (ou
      `[indecifrável]`), nunca 500, e o código da conta e o do banco, que estão em
      claro no snapshot, continuam; as **gerações do arquivo contábil** (S13,
      `accounting_file_generations`, só metadados e SHA-256) também FICAM no
      encerramento, legíveis, sem gerar nem baixar (o histórico dos itens não é mais
      legível, então o arquivo não se reproduz); na exclusão definitiva saem ANTES das
      materializações (apontam para elas) e dos usuários (autoria RESTRICT);
      conciliações, valores, datas,
      categoria e carteira FICAM, só-leitura.
    - **Encerrado é TERMINAL**: cliente que volta é cadastro novo. Toda escrita
      em cliente encerrado é 409 (`ClientClosedError`) — a trava de rota é
      `OpenClientDep` (`core/dependencies.py`), e o check protege inclusive o
      **provisionamento lazy de DEK** (`crypto_service.ensure`): sem ele, uma
      escrita re-embrulharia DEK nova num tenant morto. Leitura continua com
      `AccessibleClientDep`. A exclusão total segue disponível para encerrado
      (LGPD — o titular pode exigir apagamento completo).

13. **Carteira compartilhada (86e390kku, 15/09/2026): N gerentes com ACESSO, UM
    responsável.** `client_assignments` deixou de ser 1:1. Cada linha é uma
    pessoa com acesso ao cliente; `is_primary` marca o **responsável** (o nome da
    coluna "Gerente responsável" da lista, a quem se cobra). Duas garantias **no
    banco**: `UNIQUE(client_id, user_id)` — a mesma pessoa não entra duas vezes
    (sem ela, `resolve_client_access` estoura `MultipleResultsFound`) — e o índice
    único **parcial** `uq_client_assignments_primary` (`client_id WHERE
is_primary`) — um responsável por cliente; o predicado é COPIADO na migration
    (`6bb85e6b7d72`) e `tests/unit/test_client_assignment_schema.py` prova que as
    duas fontes batem. Regras de negócio: **definir o responsável
    (`PATCH /clients/{id}/assign`) não remove ninguém** — o anterior vira
    colaborador; **remover o responsável sem definir outro é 409** (cliente nunca
    fica órfão); só `manager` ativo entra (admin já alcança tudo pela matriz);
    gerir a carteira é `EDIT_CLIENT` (admin). Na listagem, o join de EXIBIÇÃO é só
    do responsável e o filtro da CARTEIRA é `EXISTS` sobre todas as linhas — são
    duas perguntas diferentes, e reusar o join no filtro faria o colaborador sumir
    da própria lista. Linha nova em `client_assignments` marca `is_primary`
    **explicitamente** (default FALSE no ORM); o default do BANCO é TRUE de
    propósito — linha gravada sem o campo é a forma antiga da tabela (1 linha =
    o gerente), e é assim que as linhas pré-migration e as que a API antiga
    criar na janela de deploy nascem responsáveis. **Gerente** que cria o cliente
    é o responsável; **admin não entra na carteira** (nem na criação — já alcança
    tudo pela matriz): o cliente nasce sem responsável e o primeiro gerente
    adicionado assume. As escritas são condicionais no próprio SQL (promover com
    `RETURNING` sob `FOR UPDATE`; remover só `WHERE is_primary = false`) — o
    409/404 é decidido pelo estado atual, não por uma leitura anterior.
    **A carteira é intra-org** (86e36ecjp): `is_active_manager` exige gerente ativo
    **da mesma organização do cliente**, e é a única validação consumida por criar
    cliente, adicionar gerente e definir responsável — gerente de outra organização
    recebe o MESMO 400 de "não é gerente" (anti-enumeração). Onde o cliente nasce
    vem da LINHA do ator: staff cria na própria organização (`organization_id`
    alheio no payload é 403, nunca ignorado); só a plataforma escolhe, e a escolha é
    obrigatória e validada (existe, ativa).

---

## 5. Regras Invioláveis de Domínio (Matching)

> **Como o cruzamento decide** (passadas por proximidade de data, desempate por valor →
> afinidade de fornecedor → data, o caso real que originou a regra, e o protocolo para
> mudar o motor sem regredir): skill **`matcher`**. **Nomenclatura e sinal do Omie**
> (natureza `P`/`R` com valor já sinalizado no cartão E na conta corrente, apesar de a doc
> dizer `D`/`C`; as duas formas da linha de saldo; `cSituacao` canônico
> contra o filtro em UPPERCASE, o `"PREVISTO"` que não existe como filtro): skill
> **`omie`**.

1. **Tolerância de valor:** `|a − b| ≤ 0.01 BRL`. Hard-coded, não parametrizável.
2. **Tolerância de data:** **fixa, não parametrizável** — `DATE_DIVERGENCE_RANGE = 3` no
   matcher. Classificação por `|days_diff|`: `== 0` → `conciliado`; `1–3` →
   `conciliado_data_divergente` (+ anomalia `wrong_date`); `> 3` → sem match
   (`sem_omie`). Vale para conta corrente **e** cartão. O request não aceita mais
   `date_tolerance_days`; a coluna homônima é histórico e novas sessões gravam 0.
3. **Período Omie expandido** em `DATE_DIVERGENCE_RANGE` nas duas pontas — e isso vale em
   **cinco pontos de chamada, quatro consumidores**: processamento, cache da
   qualificação, tela de revisão, export e detalhe de lançamento. Mudar num só cria
   divergência silenciosa entre o que o matcher viu e o que a tela mostra.
4. **Um OmieEntry só matcha uma Movement** — cruzamento 1-para-1, garantido no banco pelo
   índice parcial `ix_recon_file_entry_session_omie_unique`.
5. **Idempotência:** `UNIQUE(client_id, omie_conta_id, reference_month, file_hash)`.
   Duplicata = HTTP 409 `DUPLICATE_FILE`.
6. **IA nunca decide match.** A IA só extrai do arquivo; o cruzamento é código
   determinístico, sem heurística e sem modelo.
7. **Fornecedor e descrição trafegam em memória** durante o cruzamento — não são
   persistidos nem logados (§4.5).

---

## 6. Regras Invioláveis de Integridade (Anti-Alucinação)

**Estas regras existem para garantir que cada entrega seja confiável. Nunca as viole. Preferir admitir "não sei" a inventar é regra absoluta — fingir competência custa mais caro do que confessar dúvida.**

**Princípio-guia:** nada pode ser feito sem estar muito bem definido antes; nada pode ser entregue sem verificação. "Propriedade" = ter base concreta (leitura de código, output de comando, doc oficial) pra cada afirmação.

_**Definir ANTES de fazer:**_

1. **Antes de qualquer implementação não-trivial, alinhe o escopo com o usuário.** Use `AskUserQuestion`, Plan mode, ou texto explícito pedindo confirmação. "Bem definido antes de fazer" não é opcional.
2. **Spec ambígua → pergunte.** Se a especificação deixa dois caminhos válidos, NÃO decida sozinho. `AskUserQuestion` é a ferramenta certa.
3. **Pesquisa preliminar é parte do trabalho.** Antes de delegar a agente-filho ou começar a codar, investigue o estado atual do código (Read, Grep, Glob, Explore). Repasse achados explícitos — ver [[feedback_prompts_em_fatias]].
4. **Mudança em sistema desconhecido = leia primeiro.** Antes de editar módulo que você não viu nesta conversa, abra o arquivo e leia o contrato. Sem exceção.

_**Verificar ANTES de afirmar:**_

5. **Nunca cite identificador (função, classe, endpoint, env var, biblioteca, comando, flag, arquivo, módulo, hash, ticket) sem ter confirmado que existe.** Read/Grep/Glob/`gh`/`git` provam a existência. Se não pode verificar agora, escreva "(a confirmar)" explícito — não chute.
6. **Nunca invente assinatura de função** (parâmetros, tipos, defaults, retorno). Leia o arquivo onde está declarada antes de chamar/sugerir.
7. **Nunca invente comportamento de biblioteca de terceiros.** Confirme na documentação oficial atualizada — APIs mudam, conhecimento de treinamento envelhece.
8. **Omie API é especialmente perigoso.** Sempre validar contra response real, **nunca** contra `Docs/documentation/6` ou doc interna — já temos histórico de campos divergentes ([[feedback_omie_validate_response_not_internal_doc]]). ↳ skill `omie`.
9. **Conhecimento de treinamento NÃO é fonte da verdade.** Para qualquer fato técnico (versão de lib, sintaxe de framework, comportamento de SDK), verifique no projeto ou na doc oficial **antes** de afirmar.

_**Verificar ANTES de declarar pronto:**_

10. **Nunca diga "está funcionando" sem ter rodado.** Testes locais ou o comando do CI — cite o output real, nunca "deve passar". ↳ skill `gate`.
11. **Nunca invente número de testes, IDs de commit, status de CI, conteúdo de log, ou tamanho de diff.** Se vai citar, mostre o output real (`gh run view`, `git log`, output do pytest, `git diff --stat`).
12. **Nunca afirme que um arquivo foi criado/modificado sem ter executado a tool com sucesso.** Tool falhou, foi negada, ou nem foi chamada = tarefa não feita. Não relate como entregue.
13. **Verifique commit hashes via `git log` antes de citar.** Hashes mudam após rebase/amend — não confie em memória da própria conversa.

_**Honestidade ao reportar:**_

14. **Quando não souber, diga "não sei" e explique o que falta pra responder.** Não improvise. "Acho que" sem base é alucinação disfarçada.
15. **Quando estimar (tempo, custo, performance), explicite que é estimativa e mostre a base do cálculo.** "~5h baseado em S14 que foi 4h30 + sub-task UI" é estimativa válida; "~5h" sozinho é palpite.
16. **Quando um teste falhar de forma estranha, NÃO mude assertion pra fazer passar.** Investigue root cause. Esconder falha é alucinar competência — e o bug aparece em prod.
17. **Quando uma decisão de design comprometer algo (segurança, integridade, performance, UX), avise no momento da decisão.** Não enterre o trade-off em silêncio.
18. **Se o usuário pediu A e você fez B, declare a mudança e o motivo.** Nunca relate B como se fosse A.

_**Quando o ambiente discordar do que você "sabe":**_

19. **Conflito entre treinamento/memória e código atual: confie no código atual.** Código é fonte da verdade; treinamento é desatualizado; memórias caducam — ver disclaimer de memória do próprio agente.
20. **Erro inesperado de tool = pare e investigue.** Não tente "outro jeito" sem entender o que falhou — pode esconder bug real (permissão, path errado, racing).
21. **Escopo crescente durante implementação: pare e pergunte.** Se aparecer refactor adjacente não pedido, NÃO execute em silêncio. Mostre, pergunte, espere confirmação.
22. **Quando o output de uma tool não casar com expectativa, releia o output literalmente.** Não interprete "no output" como "deu certo" — pode ser stderr vazio com exit code != 0.

_**Sanity-check antes de finalizar resposta:**_ antes de apertar enviar numa resposta longa, releia mentalmente — toda função/arquivo/hash/número citado tem base concreta nesta conversa (output de tool, leitura de arquivo, doc oficial)? Estou reportando o que **fiz** (verificável no diff/log) ou o que **pretendia fazer**? Há alguma afirmação que o usuário poderia ler como certeza, mas eu não verifiquei? Qualquer "sim" pra "inventei" = pare, verifique, ou reescreva.

---

## 7. Padrões Obrigatórios

### Backend

- **Type hints em 100 %** do código. Mypy strict no CI.
- **`async def`** para tudo que toca I/O. `def` síncrono apenas em funções puras (matcher, crypto, formatters).
- **Módulos de domínio** seguem padrão `routes.py / service.py / repository.py / schemas.py`.
- **Exceptions custom** (`AppError` → `DuplicateFileError`, `OmieAuthError`, etc.) com `code` e `user_message`. Exception handler global converte para formato §9 do PLANO.
- **Dependency Injection** via `Depends`. Proibido estado global.
- **Lint obrigatório:** ruff (`E, F, I, N, W, UP, B, C4, SIM, RUF, S, A, ASYNC, ANN, PT, TID`) + ruff format (line-length 100), mypy strict.
- **Falha esperada nunca é 500** (Sprint 12, ADR-078-BE: quatro caminhos para 500 pegos
  pelo QA, nenhum por teste sequencial):
  - validar a FORMA não basta: o padrão tem de recusar o que o construtor não aceita
    (`COMPETENCE_PATTERN` aceitava `0000` e `date(0, …)` estourava). Teste de ida e volta
    `format(parse(x)) == x` para tudo que o padrão aceita. E o limite do schema é o da
    COLUNA de destino: `pattern` reusado de outro campo traz o tamanho do outro campo (S16:
    `DESTINATION_TYPE_PATTERN` aceita 60, `source_type` é `String(30)` → 500). Todo `str`
    que vai para coluna `String(n)` leva `max_length=<constante da coluna>`, com teste que
    amarra os dois;
  - props de métrica montadas DEPOIS de um commit de negócio ficam dentro do fail-soft
    (`UsageEventService._props_or_none`): a métrica nunca derruba a escrita já gravada;
  - arquivo de terceiro (planilha, zip, XML): QUALQUER falha de abertura OU de iteração
    é o mesmo 400 com `raise … from None` (a exceção original traz o texto da célula e o
    handler de 500 loga `exc_info`); custo limitado ANTES de iterar (tamanho
    descomprimido, colunas, linhas PERCORRIDAS, vazias inclusive); parse síncrono via
    `run_in_threadpool`; código longo é recusado, nunca truncado;
  - **recusa de arquivo de terceiro nomeia o VOCABULÁRIO NOSSO e conta o resto**
    (86e3fvffy): o `details` de uma recusa pode listar coluna do modelo, coluna do
    mapeamento, número de linha e motivo fechado — tudo que a plataforma já conhece —,
    e o que veio do ARQUIVO sai como CONTAGEM (`foundColumnCount`,
    `unexpectedColumnCount`). "Nome de coluna é estrutura, não PII" é falso quando o
    arquivo vem SEM cabeçalho: aí a linha 1 é dado, e devolvê-la ecoa nome de conta ou
    descrição de lançamento — que nascem cifrados (§4.1/§4.5). Filtrar por heurística
    ("parece cabeçalho": sem dígito, curto) não resolve, porque nome de conta passa no
    teste. A exceção é `SEM_MAPEAMENTO`, onde mostrar as colunas É a função da resposta
    (a tela constrói o mapeamento a partir delas) — exceção declarada, não esquecimento;
  - **coluna com tamanho fixo derivado de uma constante usa a CONSTANTE, nunca um
    literal** (86e3g9v0j): `title_contexts.text_iv` nasceu `String(32)` na migration da
    S15 enquanto o modelo e as outras 16 colunas `_iv` diziam `IV_HEX_LENGTH = 24`. Não
    quebrou nada (24 cabe em 32), mas o `alembic check` passou a acusar drift em toda
    entrega, e o próximo `--autogenerate` emitiria o ALTER sozinho dentro de outra
    sprint. Migration aplicada não se reescreve: a correção é migration nova. O que
    impede a terceira vez é teste que varre o metadata
    (`tests/unit/test_iv_column_length.py`), não revisão;
  - UNIQUE que a corrida alcança (duplo clique, duas abas): `ON CONFLICT DO NOTHING` para
    ação idempotente, SAVEPOINT + nome da constraint → 409 para escrita. Checar antes de
    inserir não protege nada sob concorrência.
- **"O mês de agora" é no fuso do Brasil**, num lugar só:
  `client_movements/competence.py::current_competence` (UTC-3 fixo, como o export). Em
  UTC, das 21h às 23h59 do último dia o servidor já está no mês seguinte.
- **Teste de integração que não rodou não é verde.** Sem Postgres no sandbox, o HANDOFF diz
  "escrito e NÃO executado" e o QA roda (receita no ADR-032-QA). Releitura em teste async:
  `execution_options(populate_existing=True)` ou `refresh(obj)`, nunca `expire_all()`.
- **UPDATE em lote por pk com WHERE extra é Core, não ORM** (Sprint 14, ADR-042-QA):
  `update(Modelo.__table__)` com `client_id` no WHERE. O ORM bulk UPDATE com WHERE levanta
  `InvalidRequestError: bulk synchronize of persistent objects not supported` com sessão
  real, e o unitário que mocka o repositório não enxerga: foi um 500 em TODO envio de arquivo.
- **Dinheiro vindo de arquivo de terceiro é validado no formato ESTRITO do separador
  declarado**, com teto derivado da coluna e `InvalidOperation` capturada `from None`:
  `1E+30`, CNPJ ou `1500.50` sob vírgula decimal recusam o arquivo com 422, nunca 500 e
  nunca aceite silencioso (o `1500.50` virava R$ 150.050,00).
- **Log da aplicação se afirma com `structlog.testing.capture_logs`, não com `caplog`**: o
  `caplog` não vê o structlog, e o teste "nada de PII no log" passa vazio.
- **A resposta HTTP só sai depois do `commit()` — sempre, em toda rota, por um middleware
  ÚNICO, nunca por commit espalhado** (86e3fxqqa): `get_db_session` faz `commit()` no
  pós-`yield`, e o FastAPI instalado só fecha esse `AsyncExitStack` **depois** de
  `await response(...)` já ter despachado a resposta (confirmado lendo
  `fastapi/routing.py::request_response` do pacote instalado — nunca suponha isso de
  memória, a versão pode mudar o mecanismo). Sem tratamento, o cliente vê sucesso antes do
  dado existir — 45 de 100 criações medidas em servidor real. A
  `CommitBeforeResponseMiddleware` (`app/core/response_ordering.py`) comita **dentro do
  `send`, ao ver o `http.response.start`**: é o instante exato entre "resposta pronta" e
  "primeiro byte no socket". A session chega até lá por `request.state.db_session`,
  publicada pelo próprio `get_db_session` — que também a ZERA no `finally`, senão a
  resposta de ERRO (montada depois do teardown ter dado rollback) faria o middleware
  comitar uma session revertida.
  ⚠️ **Não bufferize a resposta inteira para conseguir a mesma ordem** — foi a primeira
  tentativa desta task e ela **segura a resposta até a BackgroundTask terminar**: o
  Starlette roda `await self.background()` DENTRO de `Response.__call__`, depois dos
  `send`. Nos 4 endpoints de conciliação isso é o cliente esperando o processamento
  inteiro (teto de `RECONCILIATION_TIMEOUT_SECONDS`, 900 s) em vez do 201. Medido: 3,00 s
  contra 0,00 s numa task que dorme 3 s. **Nenhum teste da suíte pega isso sozinho** —
  todos substituem `_schedule_reconciliation_processing` por um stub; por isso existe o
  cenário com BackgroundTask REAL em `tests/integration/test_response_ordering.py`. As
  outras duas alternativas também não servem: `BaseHTTPMiddleware` retorna do `call_next()`
  no primeiro chunk (estreita a corrida, não elimina) e `APIRoute` customizada exigiria
  `route_class=` em CADA `APIRouter()` dos módulos, porque `include_router()` sempre recria
  a rota com `route_class_override=type(route)`.
  Provar qualquer coisa disso em teste exige **servidor uvicorn real**:
  `httpx.ASGITransport` roda a app inteira numa única coroutine e não reproduz a corrida
  nem sem middleware nenhum — só um socket de verdade separa "bytes no cliente" de "o
  resto da coroutine do servidor continua".

### Frontend

> **O roteiro de UI inteiro vive na skill `front-gate`**: área rolável (`<ScrollRegion>`
> para card, `<TableCard>` + `<Table fill>` para tabela, ou `<Table stickyHeader="page">`
> quando o que fica acima da tabela não cabe junto com ela), autorização na tela por
> `lib/authz.ts`, cor só por token semântico, os três temas, tooltip que nunca é `title`
> nativo, e como rodar o gate de a11y de verdade. O que segue vale mesmo sem abrir a skill:

- **TypeScript strict** + `noUncheckedIndexedAccess`. **Server components por padrão**;
  `"use client"` só quando há estado, efeito ou evento.
- **Fetch client-side sempre via TanStack Query**, nunca `useEffect + fetch`. **Forms
  sempre `react-hook-form + zod`.** Tabela acima de 100 linhas é virtualizada.
- **A UI não é barreira de segurança** (§4.9) — mas mostrar ação que o servidor nega é
  defeito: cada ❌ da matriz precisa de bloqueio no backend **e** de ação oculta na tela.
  **Aviso de OUTRA tela que aponta para a ação passa pelo mesmo gate** (S16, ADR-053-FE):
  texto com verbo de ação ("Importe", "Associe") e o botão só para quem tem a permissão;
  os demais leem um texto informativo. Quem orienta recebe a permissão e o encerramento
  SEPARADOS, porque o motivo muda o texto. Chave React sobre lista crua do backend leva a
  posição.
- **Gate de a11y verde NÃO prova layout.** O axe mede semântica e contraste, não
  transbordo: toda task de UI termina com o screenshot desktop e 390px aberto e conferido.
- **Item de menu cuja LEITURA não pede permissão não se esconde por estado de dado**
  (86e3fqnc9). Esconder a aba "Origem por arquivo" de quem ainda não tinha a conexão
  deixou o recurso invisível para exatamente quem precisava descobri-lo, e não era a
  regra da §4.9: a rota é `AccessibleClientDep`, ninguém seria negado ao abri-la. Ação
  que o servidor nega continua oculta — mas isso se decide DENTRO da tela, por estado, e
  a tela explica o que é o recurso, por que ele não está disponível ali e quem pode
  liberar. Quem some do menu é o que a MATRIZ nega, nunca o que o dado do cliente ainda
  não tem.
- **Ação oferecida numa tela TEM de existir na tela de destino, e quem decide é a MESMA
  chamada dos dois lados** (86e3g9uku · 86e3g9u3w). O painel do cliente oferecia "Criar
  conciliação" apontando para a lista, onde a criação já estava escondida para cliente
  sem origem e para cliente só-arquivo; a gaveta de conexão oferecia um tipo que o
  servidor recusa com 409. Em toda ponte entre telas, a origem chama `originCodeFor(…)` /
  a capacidade **com o mesmo argumento** que o destino usa — perguntar diferente faz as
  duas discordarem sobre o mesmo cliente, e o rótulo do botão diz o que ele faz de fato
  ("Ir para conciliações", não "Criar conciliação", quando o link só navega).
- **A PALAVRA do estado acompanha a origem, não o verbo de uma origem só** (86e3g9ua7).
  No de-para, "Nunca sincronizada"/"Sincronizada em" num cliente por arquivo manda
  procurar um botão "Sincronizar" que a própria tela já trocou por "Enviar arquivo do
  mês" (lá `POST …/movements/sync` é 409 `ORIGEM_POR_ARQUIVO`). Texto de estado ao lado
  de uma ação condicional se ramifica pela MESMA resposta que ramifica a ação
  (`originIsFileBased`), senão a tela se contradiz em duas linhas vizinhas.
- **Dois padrões de altura para tabela, e a tela escolhe um** (86e3eq9uy): `<Table fill>`
  dentro de `<TableCard>` quando a tabela é o que enche a janela (a tabela rola por
  dentro); `<Table stickyHeader="page">` + `<TableCard pageScroll>` quando o conteúdo acima
  dela não cabe junto na viewport (a página rola, o cabeçalho gruda no `<main>` de `xl`
  para cima, a paginação vem depois da última linha, nunca grudada). `position: sticky`
  gruda no scroller mais próximo, e `overflow-hidden` também é scroller: por isso o
  segundo padrão recorta com `overflow-clip`. **A tela do segundo padrão declara
  `data-page-scroll` na própria `<section>` raiz** (86e3gkd80): é o que faz o
  `ClientShell` trocar a altura FIXA do primeiro padrão (`h-full` + `min-h-0 flex-1`)
  por altura natural, via `has-[[data-page-scroll]]`. Sem o atributo o conteúdo ainda
  rola (overflow de descendente), mas o `padding-bottom` do `<main>` só entra depois do
  filho em fluxo, e a página termina COLADA na borda da janela: o card "Conta do banco"
  do plano contábil parecia cortado, e carteira, de-para e plano de contas perdiam o
  respiro abaixo da paginação. A guarda é `exigirFimDaPaginaComRespiro` no e2e
  (`toBeInViewport` não pega: a borda colada está "na viewport").
- **A entrada do cliente é a lista de conciliações (`/clientes/{id}`); o painel mora em
  `/painel`; item do menu ativo casa pela própria rota, sem fallback** (decisão do Pedro,
  08/10/2026). Link para a lista sai de `reconciliationsPath()` e para o painel de
  `dashboardPath()`, nunca de `/clientes/${id}` à mão; o `/conciliacoes` da semana é 308.
  **A gestão de origens vive em Contas Bancárias** (`originFixPath()` →
  `/clientes/{id}/contas?conectar=<tipo>`); o painel mostra a seção inteira só sem origem
  ativa e, com ela, uma linha de estado.
- **Tela dentro do cliente traz o PRÓPRIO `<h1>`** (86e3fr9q3): o `ClientShell` não tem
  cabeçalho (sem breadcrumb, nome do cliente, selos nem menu de ações; o nome está no menu
  lateral, e Editar e Encerrar moram na linha da lista de clientes). Tela nova sob
  `/clientes/[id]` começa com o `<h1>` dela; um título no shell repetiria o da tela logo
  abaixo. Cliente encerrado ganha o aviso "Cliente encerrado: somente leitura" do shell.
- **Totais que filtram são recolhíveis pela mesma moldura** (86e3fr9qz):
  `components/shared/collapsible-summary.tsx`, com estado por tela no `localStorage`
  (try/catch, sem armazenamento abre aberto) e o conteúdo recolhido montado com `hidden`.
  Tela nova com cards de totais usa ela, nunca um "ocultar" próprio.
- **Movimento no app autenticado só na entrada de um bloco e na resposta a uma ação, pelos
  primitivos** (86e3h579d e 86e3h57a5): `ui/card.tsx` (`Card variant="elevated"`, borda em
  gradiente e hover que sobe 2 px só com ponteiro e de `md` para cima), `ui/animated-check.tsx`
  (o check que se desenha, também no toast de sucesso) e `shared/reveal.tsx` (`Reveal` e
  `useReveal`, entrada única ao aparecer; a landing usa o mesmo `observeReveal`). Toda regra
  que mexe mora em `globals.css` sob `prefers-reduced-motion: no-preference`
  (`motion-css.test.ts` reprova a que escapa); nunca aurora, ruído, loop, fade de rota, nem
  efeito em linha de tabela ou lista virtualizada. A cor de efeito no app é `--primary` (o
  verde só no Hologram), nunca `--brand`. O gate de a11y roda com `reducedMotion: 'reduce'`
  por padrão (`playwright.config.ts`); cenário que prova movimento pede `no-preference` e
  mede depois que a animação termina. Contador do menu do cliente: a contagem do De-para é
  o MAIOR "sem decisão" entre destinos, e o detalhe (por destino, por lado, por status) vai
  num tooltip com o link inteiro como gatilho (hover e foco, sem `tabIndex` na pílula).
- **Estado vazio é o `EmptyState` com vinheta** (86e3h57b5, `shared/empty-state.tsx` +
  `shared/vignettes.tsx`): moldura tracejada no bloco, sem borda dentro de `TableEmpty` ou
  de card; a ação é um slot que quem chama já decidiu pela permissão; `announce` liga
  `role="status"` quando o vazio substitui um resultado. Vazio de tabela de lista continua
  no `TableEmpty` depois da `<Table>`, nunca numa célula `colSpan` (a vinheta sumiria em 390px).
- **Dinheiro com sinal e cor é o `<Money>` de `components/shared/money.tsx`** (86e3k1q30;
  dois eixos: sinal do valor, cor do `tone`), nunca classe solta.
- **Campo de arquivo é o `FileInputField` compartilhado** (`components/shared/file-input-field.tsx`,
  86e3gkd4y), nunca `<Input type="file">` cru: o botão nativo não muda o cursor nem reage
  ao hover, e na demo de 29/09 o "Escolher arquivo" das gavetas de importar não parecia
  clicável. Gatilho com `buttonVariants`, anel de foco por `peer-focus-visible`, nome +
  tamanho + "Remover" com arquivo escolhido, `ref` do RHF chegando ao input real. Só o
  multi-arquivo inline da gaveta de conciliação fica fora (detalhe na skill `front-gate`).
- **O `accept` não esconde formato que o servidor recusa COM MOTIVO: selecionável não é
  aceito** (86e3gkd50). Na demo de 29/09 o plano exportado do Domínio (`.xls`) nem
  aparecia no seletor, e a pessoa concluiu que o arquivo tinha sumido: filtrar no `accept`
  é a recusa silenciosa que a Sprint 2 proibiu. A lista do navegador
  (`SELECTABLE_FILE_EXTENSIONS` em `lib/validation/file-origin.ts`) é a fonte do
  `FILE_ACCEPT` e da checagem do zod, as duas deixam o `.xls` passar, e quem recusa é o
  servidor, com o 422 `FORMATO_NAO_SUPORTADO` e a instrução de salvar como XLSX ou CSV.
  Recusar de novo no zod mostraria uma cópia da mensagem, que pode divergir, no lugar da
  do servidor. O rótulo continua dizendo os formatos ACEITOS (".csv ou .xlsx").
- **Página pública de marca é o grupo `(public)` e o login, com as próprias regras**
  (86e3fr9vz; login desde a 86e3h1h75): landing `/` e `/privacidade` passam no
  `middleware.ts` sem cookie (`PUBLIC_PATHS`; com sessão, `/` vai para `/clientes`, e
  `/login` com sessão também). Os dois layouts (`(public)/layout.tsx` e
  `(auth)/layout.tsx`) usam tema Hologram FIXO pelo wrapper `.hologram lp-public` (sem
  seletor; o `<html>` guarda o tema salvo, que volta a valer depois de entrar) e a janela
  rola. A landing tem largura contida (`max-w-6xl`); o login tem duas colunas de `lg` para
  cima, o **painel de marca** (`components/landing/sign-in-brand-panel.tsx`: a aurora em
  intensidade baixa, a logomark, a frase e os chips do hero) e o card estreito
  (`max-w-sm`, logomark e título dentro, texto neutro de organização), e abaixo de `lg`
  só o card. Efeitos só em CSS escopado: o que as duas páginas dividem (variáveis `--lp-*`,
  aurora, grade, palavra em gradiente, borda em gradiente dos cards) mora em
  `app/public-brand.css`, o resto da landing em `app/(public)/landing.css`, sempre em
  fundo, borda, sombra e ícone, nunca em texto corrido, mais UM componente cliente de
  efeitos (`components/landing/reveal.tsx`). A cor de destaque é o **verde da Hologram**,
  o token `--brand` do `globals.css` (`#05d1bf`, amostrado por pixel de
  `Docs/brand/site-hologram-cta-2026-09-30.png`; mesmo valor nos três temas, com
  `--brand-foreground` e `--brand-hover`), e o botão primário das páginas públicas é o
  `<Button variant="brand">`: verde com texto navy (cópia literal do `--primary` claro),
  hover sólido, anel de foco no próprio verde, desabilitado a 50 %. **Nunca texto branco
  sobre o verde** (1,85:1); os pares ficam em `theme-contrast.test.ts`. No app autenticado
  o verde só entra como cor de AÇÃO do tema Hologram (item seguinte), nunca para vestir
  tela de dado. Nada pinta
  atrás do texto de card (o spotlight saiu na 86e3h0xcr); tudo parado sob `prefers-reduced-motion`, que é também o estado que o gate de a11y mede.
  **Um efeito por bloco, e loop só enquanto o bloco está na tela** (86e3gwzj0): o "antes"
  de cada entrada mora sob `prefers-reduced-motion: no-preference`, e nenhum efeito
  novo entra sem medição por pixel quando pinta atrás de texto. O tour (86e3gqfkf) usa
  **prints reais com dado fictício** de `Docs/manual/fonte/img/` (figura com nome de dado
  de teste não entra), copiados otimizados para `public/landing/tour/` e servidos com
  `next/image` `unoptimized`: o web é `standalone` sem `sharp`, e nesse modo o otimizador
  do Next 14 responde 500. Todos os prints do tour estão no tema Hologram. O estado das
  abas é o único outro JS de efeito (`landing-tour-tabs.tsx`): uma linha de progresso na aba
  ativa explica a troca automática e o botão de pausar é só ícone (WCAG 2.2.2). Texto
  da landing só em `components/landing/content.ts` (espelho comentado em
  `Docs/landing/COPY.md`), com tetos de caracteres por campo travados em
  `content.test.ts`; os textos novos do login moram lá também (`login`). Nada disso vale
  para o app autenticado.
- **No tema Hologram o primário é o verde da marca; a logomark e o link têm token
  próprio** (86e3h5783 + 86e3h578n): `--primary`, `--ring` e `--link` valem o `--brand`
  no bloco `.hologram`, com o navy por cima (`--primary-foreground`). `--logo` pinta a
  `BrandMark` (`text-logo`; o primário no claro e no escuro, BRANCA no Hologram), `--link`
  pinta link de ação em texto (`text-link`; o `foreground` no claro e no escuro) e
  `--primary-hover` é o hover sólido do `Button`/`Badge` `default` (no claro e no escuro, o
  valor que o antigo `/90` dava). Claro e escuro não mudaram; `--accent` segue o tint do
  navy. Ícone e indicador de ação seguem o primário; texto corrido e rótulo informativo
  não (exceção decidida: o chip "Destaque" da categoria).
- **Nome do produto só em `lib/brand.ts` (web) e `core/branding.py` (API)** (86e3fr9x3):
  `PRODUCT_NAME = 'Hologram OS'` (título, header, login, landing, metadados, título do
  OpenAPI "Hologram OS API", prefixo `[Hologram OS]` dos alertas, textos operacionais) e
  `COMPANY_NAME = 'Hologram Gestão'` (quem trata o dado, rodapé). `brand.test.ts`,
  `test_branding.py` e `content.test.ts` recusam o nome ANTIGO e a sigla ADL em string
  (literais escritos no próprio teste) e o nome novo escrito à mão fora dos arquivos de
  marca. URL absoluta só por `NEXT_PUBLIC_SITE_URL` (`lib/site-url.ts`): sem ela não há
  `metadataBase`, canônica nem sitemap, e o domínio nunca é montado a partir de
  `PRODUCT_DOMAIN`.

### API

- **Response de sucesso:** `{ "data": {...} }` ou `{ "data": [...], "pagination": {...} }`.
- **Response de erro:** `{ "error": { "code", "message", "userMessage" } }`.
- **Rotas:** `/api/v1/...`.
- **Paginação:** `?page=1&pageSize=20`, max 100. No FastAPI, o parâmetro **precisa** de `alias="pageSize"` (`page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20`). Sem o alias o nome que entra pela URL vira `page_size`, o `pageSize` do front é descartado **sem erro** e o servidor devolve o default — o seletor de itens-por-página fica de enfeite. Foi o que aconteceu nas 3 rotas da revisão até 14/08/2026 (86e2u512z).
- **Códigos canônicos:** ver §9 do PLANO. Usar constants centralizadas, nunca strings mágicas.

### Commits / Git

- **Conventional Commits** (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`).
- **Branch:** `feat/S3-login-endpoint`, `fix/S11-cache-invalidation`.
- **PR com ≥ 1 review** em `main` protegida.
- **Nunca** `git push --force` em main, `--no-verify`, `--no-gpg-sign`.

### CI/CD verde (GitHub Actions)

> **Os comandos, como ler o resultado e o que fazer quando o CI falha estão na skill
> `gate`** — inclusive as falhas que são do ambiente e não do código, o teste flaky e os
> hooks locais do commit.

- **`main` precisa terminar com CI verde sempre.** A esteira (`.github/workflows/ci.yml`)
  roda os dois lados mesmo quando o PR toca um só — não dá para esconder regressão atrás
  de filtro de path.
- **Rode o gate local ANTES de cada push** e cite o output real: "deve passar" não fecha
  task (§6.10).
- **Nunca** `git push --force` em `main`, **nunca** `--no-verify`, **nunca** mudar
  assertion para o teste passar (§6.16).

### Idioma

- **Código:** inglês.
- **Comentários/docstrings:** português quando clarificam domínio de negócio; inglês para tecnologia pura.
- **Mensagens ao usuário final:** **sempre** português.
- **Mensagem de commit:** **inglês (EN-US)**, corpo e rodapé inclusive. O formato Conventional Commits (§7 · Commits / Git) **não muda** — traduza a mensagem, não o padrão.
- **Nome de branch:** **inglês (EN-US)**, mantendo o padrão `fix/S17-redactor-token-counts`, `feat/S3-login-endpoint`.
- **Título e corpo de PR:** **sempre** português. A separação é deliberada: commit e branch são artefatos do repositório, o PR é a conversa do time.

---

## 8. Mapa de Sessões (referência rápida)

| Sessão  | Foco                               | Tarefas do backlog            |
| ------- | ---------------------------------- | ----------------------------- |
| **S0**  | Setup monorepo + Docker + CI       | —                             |
| **S1**  | Core: crypto, JWT, logging, errors | —                             |
| **S2**  | DB: models, migrations, seeds      | —                             |
| **S3**  | Autenticação                       | BACK 1.1, 1.2 · FRONT 1.3     |
| **S4**  | Gestão de usuários                 | BACK 2.1 · FRONT 2.2          |
| **S5**  | Cliente Omie base                  | — (fundação)                  |
| **S6**  | CRUD de clientes BPO               | BACK 3.1–3.5 · FRONT 3.7, 3.8 |
| **S7**  | Detalhe cliente + cache L1         | BACK 4.1, 4.2 · FRONT 4.3     |
| **S8**  | Formulário + validações            | FRONT 5.1, 6.1 · BACK 6.2     |
| **S9**  | Parsing Claude                     | BACK 7.1 · FRONT 7.2          |
| **S10** | Processamento async (BG Tasks)     | BACK 8.1–8.6 · FRONT 8.7      |
| **S11** | Revisão — backend + cache L1       | BACK 9.1–9.10                 |
| **S12** | Revisão — estrutura + aba 1        | FRONT 9.11–9.14               |
| **S13** | Revisão — abas 2, 3, 4             | FRONT 9.15–9.17               |
| **S14** | Exportação Excel                   | BACK 10.1                     |
| **S15** | Tipos de anomalia                  | BACK 11.1 · FRONT 11.2        |
| **S16** | Hardening de segurança             | — (transversal)               |
| **S17** | Observabilidade                    | — (transversal)               |
| **S18** | E2E + deploy + docs                | — (finalização)               |
| **S19** | Qualificação (`qualification`)     | BACK 12.1 · FRONT 12.2        |

> **S20+ (pivot — auditoria contínua sobre o Omie):** eixo S20–S27, em planejamento. Não está na tabela acima; ver [Docs/PLANO_S20_AUDITORIA_CONTINUA.md](Docs/PLANO_S20_AUDITORIA_CONTINUA.md).

### Sprints do agents-hub (numeração própria, todas na `main`)

Rodadas pelo orquestrador multi-agente; o escopo de cada uma vive no **Doc do
ClickUp**, não no repo. `make sprints` lista o estado.

| Sprint | Foco                                       | Deixou no código                                                                                                                                                                      |
| ------ | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **0**  | Estabilização                              | —                                                                                                                                                                                     |
| **1**  | Fatura de cartão + conta aplicação         | `account_type`, `DATE_DIVERGENCE_RANGE`                                                                                                                                               |
| **2**  | Parsing sem perda silenciosa               | CSV grande, XLSX completo                                                                                                                                                             |
| **3**  | Cripto por cliente, auditoria, alerta      | `clients.dek_wrapped`, `access_audit`, `core/kms.py`                                                                                                                                  |
| **4**  | Lista, gaveta, multi-arquivo, notificações | `reconciliation_files`, `usage_events`, `notifications`                                                                                                                               |
| **5**  | Multi-tenancy e papéis de cliente          | `users.scope`/`client_id`, `core/authz.py`, `core/sensitive_endpoints.py`                                                                                                             |
| **6**  | Glossário e classificação por cliente      | `client_glossary_entries`, `clients.glossary_version`, `review_verdict`                                                                                                               |
| **7**  | Lançamento de faturas no Omie              | `reconciliation_omie_postings`, `omie_posting/`, `OMIE_POSTING_ENABLED`                                                                                                               |
| **9**  | Cliente sem sistema e conexões plugáveis   | `client_connections`, `integrations/providers/`, `legacy_fallback.py`                                                                                                                 |
| **10** | Plano de contas do cliente                 | `client_chart_of_accounts`, `modules/client_chart_of_accounts/`, `clients.chart_of_accounts_synced_at`                                                                                |
| **11** | Carteira de títulos em aberto              | `client_titles`, `modules/client_titles/`, `clients.titles_synced_at`/`titles_sync_failed_at`                                                                                         |
| **12** | De-para multi-destino                      | `client_movements`/`client_movement_syncs`, `mapping_destinations`/`mapping_targets`, `client_mapping_decisions`, `client_mapping_materializations(_items)`, aba "De-para"            |
| **14** | Origem por arquivo (cliente sem ERP)       | provedor `arquivo` (`file_adapter.py`), `client_input_mappings`, `client_file_categories`, `client_file_imports`, `client_movements.description_encrypted`, rota "Origem por arquivo" |
| **15** | Contexto do título: acordo × inadimplência | `title_contexts`, rotas `/titles/{id}/context` e `/titles/receivables-report`, aba "Relatório de recebíveis"                                                                          |
| **13** | Exportador para sistema contábil           | `export_layouts`/`export_layout_versions`, `accounting_file_generations`, `modules/export_layouts/` e `accounting_files/`, "Gerar arquivo" no de-para, tela "Layouts de exportação"   |
| **16** | Plano contábil do cliente e partida        | `client_accounting_accounts`, `client_source_account_bindings`, `history_encrypted`, snapshot com conta, banco e vigência, tela "Plano contábil"                                      |

**A Sprint 12 (de-para multi-destino)** criou a peça central da plataforma: a decisão
`(cliente, tipo de origem, categoria, destino) → alvo`, em que a MESMA categoria vai para
destinos diferentes (ex.: transferência entre contas próprias é `nao_mapear` no
demonstrativo e alvo real no fluxo de caixa). O que vale como lei, não como detalhe:

- **a base é `client_movements` (R0)**, agnóstica de origem e só com CÓDIGOS (nenhum nome
  nem texto livre; a exceção é a linha vinda de ARQUIVO na S14, que traz a descrição
  cifrada com a DEK do cliente, §4.1), sincronizada por competência (`POST /clients/{id}/movements/sync`),
  num ciclo que nunca apaga (`ausente_na_origem`); o de-para NUNCA lê as divergências da
  conciliação nem a origem ao vivo. A S14 alimenta a MESMA tabela com `source_type=arquivo`;
- **`tipo de origem` é o tipo do provedor**, nunca FK de conexão: recriar a conexão não
  leva o de-para junto;
- **três estados distintos:** `nao_mapear` é decisão (linha), "sem decisão" é ausência de
  linha, "sem categoria de origem" é buraco de ingestão e fica FORA do denominador da
  cobertura;
- **vigência por competência, append-only** (`resolve_vigente`, função pura única);
  retroativa sobre competência materializada é 409;
- **alvo é código de catálogo por organização** (5 tipos semeados; o 6º é cadastro,
  não migration); herança só no `demonstrativo_contabil`, a partir do `dre_code` do
  plano de contas (S10);
- **materialização imutável** `(cliente, destino, competência, versão)`, gravada só com o
  hash da prévia confirmada — é o que a Sprint 13 lê. `depara_aplicado` e
  `movimentos_sincronizados` são os eventos da métrica (sem dedup, só IDs e números).

Números deste primer: endpoints sensíveis 77 → **97**, matriz 20 → **23**, pares de AAD
seguem **13** (nenhum campo cifrado nasceu: a base só guarda códigos). O QA reprovou 5
das 7 tasks na rodada 1 (testes de integração nunca rodados e quatro caminhos para 500,
ADR-039-QA) e aprovou as 7 na re-revisão de 26/09, com a suíte completa contra Postgres
verde (2898 passed; ADR-040-QA). As lições viraram regra na §7 Backend.

**A Sprint 14 (origem por arquivo)** atende o cliente que NÃO tem ERP: a planilha ou o
extrato do mês entra pela tela "Origem por arquivo", é lido por um **mapeamento de
entrada** declarado uma vez (qual coluna é data, descrição, valor e categoria; formato;
convenção de sinal, nunca inferida) e vira linhas da MESMA base de movimentos da S12, com
`source_type=arquivo`. A partir dali o cliente arquivo passa pelo de-para e pela
materialização sem nenhum caminho especial. O que vale como lei:

- **o arquivo entra INTEIRO ou não entra**: cabeçalho que não bate, linha inválida ou
  total declarado divergente são 422 tipados, com o motivo acionável na resposta (colunas
  ausentes, linha × motivo de vocabulário fechado, nunca a célula) e nenhum movimento
  gravado; o mesmo arquivo na mesma competência é 409; um arquivo CORRIGIDO na mesma
  competência substitui, e as linhas do anterior viram `ausente_na_origem`;
- **a categoria de origem do arquivo é a grafia da célula**, cifrada
  (`client_file_categories`), com código derivado e estável por cliente: `Despesas
Bancárias` e `Despesas bancárias` são DUAS categorias, de propósito: a ingestão não
  funde grafias, e cada uma recebe a própria decisão no de-para;
- **um cliente, um tipo de origem de lançamentos** (§4.8), e as telas escondem pela
  CAPACIDADE (`lib/origin-capabilities.ts`), nunca por `provider_type === 'arquivo'`;
- a métrica é `fechamento_produzido{tipo_origem}` (uma linha por competência
  materializada) e a instrumentação da ingestão é `arquivo_processado` (seis chaves, só
  IDs, contagens e um motivo fechado; nunca nome de coluna nem conteúdo de célula).

Números deste primer: endpoints sensíveis 99 → **104**, matriz 24 → **26**, pares de AAD
13 → **15**. O QA reprovou 5 das 6 tasks na rodada 1 (um 500 em TODO envio de arquivo,
invisível ao unitário; ADR-041-QA) e aprovou na rodada 2 SEM Docker, por revisão estática
e unitários (ADR-042-QA). A validação humana (86e3fcp76, 28/09/2026) rodou fora do sandbox
o que faltava: suíte completa contra Postgres, gate de a11y nos três temas, contrato,
ciclo das migrations e o cenário de ponta a ponta pela API e pela tela, e o produto
passou (3402 verdes contra Postgres, 448 por tema no a11y, 73 verificações de API). O que
ela corrigiu foi o primer, apagado pelo commit do QA como na S15.

**A Sprint 16 (plano contábil do cliente e partida completa)** prepara a Sprint 13: ela
deixa na materialização do de-para TUDO o que uma linha do arquivo contábil precisa (data,
conta de débito, conta de crédito, valor e histórico), provado contra uma amostra REAL
anonimizada de um escritório parceiro (`apps/api/tests/fixtures/accounting_sample/`: 32
movimentos, o CSV que o sistema contábil importa, Latin-1 + CRLF travado como `-text`). O
que vale como lei:

- **o plano contábil do cliente (`client_accounting_accounts`) NÃO é o plano da origem**
  (`client_chart_of_accounts`, S10): é o plano do sistema contábil de DESTINO, por cliente,
  importado por planilha no modelo da plataforma (`codigo_reduzido;nome;tipo[;classificacao]`)
  ou pelo plano EXPORTADO do Domínio em `.xlsx`, reconhecido pelo cabeçalho e convertido
  para o modelo (`client_accounting_chart/dominio.py`, 86e3gkd7y; o CSV nativo do Domínio
  não entra), tudo ou nada; reimportar casa por código e a conta que some vira inativa,
  nunca apagada; nome cifrado, código reduzido em claro;
- **no destino `conta_contabil`, e só nele, o alvo é conta ANALÍTICA e ATIVA do plano do
  próprio cliente** (`accounting_account_id`, validador único `require_postable_account`:
  outro cliente 404, sintética ou inativa 422); o catálogo da organização é recusado nesse
  destino (os códigos de cada cliente colidem entre si) e a decisão legada fica legível,
  marcada para refazer; a decisão carrega o **histórico padrão** cifrado, e trocar conta ou
  histórico é vigência nova;
- **a partida sai do SINAL**, nunca do texto (`partida.derive_partida`): entrada debita o
  banco e credita a conta decidida, saída o contrário; o lado do banco vem da associação
  conta de origem → conta contábil (`client_source_account_bindings`, com a conta PADRÃO
  para arquivo sem coluna de conta); linha com alvo sem banco resolvível bloqueia a
  materialização com 409 `CONTA_DO_BANCO_PENDENTE`;
- **o snapshot da materialização guarda o código da conta, o do banco e a vigência**
  (`decision_id`): o histórico é lido pela vigência, nunca pela atual, então reimportar o
  plano, trocar o histórico ou trocar o banco não muda o que já foi materializado;
- a métrica é a **completude de partida** (Σ|valor| com débito, crédito e histórico ÷
  Σ|valor| com alvo), por consulta ao snapshot; `plano_contabil_importado` leva só
  contagens. Nenhum arquivo contábil é gerado: isso é da Sprint 13.

Números deste primer: endpoints sensíveis 104 → **108**, matriz 26 → **27**, pares de AAD
15 → **17**. O QA reprovou 3 das 7 tasks na rodada 1 (um 500 por `sourceType` maior que a
coluna e avisos de outra tela oferecendo ação a quem não pode) e aprovou na rodada 2 SEM
Docker. A validação humana (86e3fw419, 28/09/2026) rodou o resto: suíte completa contra
Postgres (3622 testes; as 5 falhas eram de TESTE nunca executado, corrigidas), ciclo das 3
migrations, contrato com diff 0, gate de a11y nos três temas (514 cenários por tema), o cenário pela API (50
verificações; a única vermelha era a corrida descrita abaixo) e pela tela, e restaurou o primer, apagado pelo commit do QA pela terceira
vez. Ela também mediu um defeito ANTERIOR à sprint e de toda a API: a resposta sai antes do
commit da `get_db_session` (em 14 de 15 criações o `201` chegou antes do dado), o que fez a
tela ler estado velho e, uma vez, deixou uma escrita passar pela trava de cliente encerrado
logo depois do `204` do encerramento.

**A Sprint 13 (exportador para sistema contábil)** fecha o ciclo do escritório parceiro: da
materialização do de-para no destino `conta_contabil` sai o arquivo que o sistema contábil
importa, byte a byte. O que vale como lei:

- **layout é configuração versionada por ORGANIZAÇÃO**, nunca código por cliente: vocabulário
  fechado de campos (`LayoutField`, a mesma enum na validação e no gerador), parâmetros do
  arquivo validados com 422 `LAYOUT_INVALIDO` nomeando o campo; alterar grava a versão N+1 e a
  anterior fica consultável; o modelo "Domínio: lançamentos contábeis (CSV)" é dado do CÓDIGO;
  prefixo do valor e cabeçalho de coluna que começam com `= + - @` são recusados pela MESMA
  lista (`FORMULA_PREFIXES`) que recusa o texto do dado;
- **geração determinística e sem recálculo**: linhas só de `materialized_lines` (snapshot +
  histórico da vigência), partida só de `derive_partida`, ordem (data, `source_movement_id`, id
  do item); mesma materialização + mesma versão de layout = mesmos bytes e mesmo SHA-256;
- **recusa, nunca altera texto**: cobertura parcial, completude de partida < 100%, partição que
  não fecha e texto que não cabe (quebra de linha, separador, início `= + - @`, fora da
  codificação) são 409 tipados que nomeiam só CÓDIGOS de categoria; nada é substituído,
  escapado ou truncado (o `quotePrefix` do Excel não serve aqui);
- **o conteúdo do arquivo não persiste**: `accounting_file_generations` guarda só metadados e o
  SHA-256; o download REGENERA e confere (divergência = 409 `ARQUIVO_DIVERGENTE` + alerta de
  plantão, nunca um arquivo diferente); `export` na trilha na geração e em cada download;
  cliente encerrado = gerar e baixar 409, histórico legível; exclusão definitiva apaga as
  gerações antes das materializações;
- a métrica é `arquivo_contabil_gerado` (8 chaves, só IDs e números) casada com o
  `depara_aplicado` pelo `materializacao_id`, que passou a existir nele (sem backfill).

Números deste primer: endpoints sensíveis 108 → **116**, matriz 27 → **29**, pares de AAD seguem
**17**. O QA aprovou as 6 tasks na rodada 1 por revisão estática, unitários (2204 back, 1016
front), contrato com diff 0 e o teste-ouro unitário da amostra (32/32 byte a byte), SEM Docker:
integração, a11y em browser e prints ficaram para a validação humana (ADR-043-QA), junto com a
importação real no Domínio sem advertência.

✅ **A Sprint 15 (contexto do título) foi validada em 24/09/2026 à noite** (PR #203
da sprint, correções da validação em `fix/S15-validation-findings`). Ela pendura no
título da carteira um **contexto tipado e append-only** (seis tipos fechados, texto
cifrado com a DEK do cliente, autor e data) e lê esse contexto no **relatório de
recebíveis**: para a receber e a pagar, dois grupos calculados no servidor sobre a
carteira inteira, **inadimplência real** (sem contexto ou `perda_provavel`) e
**vencido com contexto**, classificando pelo contexto mais recente. O contexto
**nunca altera o título**. Números deste primer: endpoints sensíveis 74 → **77**,
matriz 18 → **20** (`view_title_context`, `manage_title_context`), pares de AAD
12 → **13**. ⚠️ Rodou pela Sprint 15 do hub, mas as Sprints 12, 13 e 14 continuam
FORA da `main`: a 12 (de-para) é a próxima do caminho crítico, a 14 depende dela e
a 13 está bloqueada pelo layout do arquivo contábil.

✅ **A Sprint 11 passou pela validação humana em 24/09/2026** (PR #200 da sprint,
correções da validação em `fix/S11-validation-findings`); depois do merge na `main`
o operador ainda precisa rodar `scripts/setup-gcp.sh dev` uma vez para criar o Cloud
Run Job e o Cloud Scheduler da carteira, senão a sincronização diária não existe.
Ela traz a **carteira de títulos em aberto**: `client_titles`
persiste, por cliente, os títulos a pagar e a receber ainda não liquidados —
**sem recorte de conta e sem recorte de competência**, que é o que faz um título
vencido há quatro meses aparecer, enquanto a conciliação (que sempre pede uma
conta e um mês) nunca o traria. `processing/omie_fetch.py` **não foi tocado**: a
conciliação continua exatamente como estava.
O que ela move nos números deste primer: a lista canônica de endpoints sensíveis
foi de 71 para **74** (`PENDING_ENDPOINTS` segue vazio) e a matriz da §4.9 foi de
16 para **18** permissões (`view_client_receivables`, `sync_client_receivables`).
⚠️ **A carteira é sincronizada por um Cloud Run Job diário**
(`scripts/sync_client_titles.py`, agendado por Cloud Scheduler) — mais um trabalho
de fundo com agendador real, no molde do `cleanup`.

✅ **As Sprints 9 e 10 estão na `main` e em dev** (S9: PR #191 da sprint e #193 do
`develop → main`, correções da validação humana em #192 e #194, credenciais de dev
convertidas e fallback desligado em 23/09; S10: PR #195 da sprint, correções da
validação humana em #196, `develop → main` em 23/09).
Cada sprint do hub termina com **validação humana** fora do sandbox: é a única
rodada real da suíte de integração, do gate de a11y e do cenário pela tela, e nas
duas primeiras ela achou o que o QA do hub não podia achar.

A Sprint 10 trouxe o **plano de contas do cliente** para dentro do produto —
códigos, hierarquia, situação, flags e, principalmente, o vínculo que a origem
**já declara** com a conta de demonstrativo (`dadosDRE.codigoDRE`), que é o
insumo do de-para da Sprint 12. Três coisas dela valem como lei, não como
detalhe de feature:

- **nome de categoria continua fora do disco** (§4.5): a tabela guarda só
  código, situação e flags; o nome é resolvido em runtime pelo MESMO
  `OmieCategoriasService` (cache de 6 h) que serve a tela de revisão. É isso que
  impede as duas telas de divergirem — e por isso a leitura da origem é **uma
  só**: `list_categorias` passou a chamar `list_raw_categorias`, e não o
  contrário. ⚠️ Código de categoria e código de conta de demonstrativo são
  **namespaces diferentes que colidem** (`1.01.01` é "BPO Controller - RB" e
  também "Receita Bruta de Vendas"): quem resolve nome trabalha com DOIS mapas;
- **dois relógios**: o cache de NOMES segue com 6 h; a PERSISTÊNCIA vale 24 h
  (`clients.chart_of_accounts_synced_at`), alinhada ao cache de contas
  correntes. "Sincronizar agora" (`force`) ignora os dois. Falha carimba
  `chart_of_accounts_sync_failed_at` e **nunca** toca o carimbo do último
  sucesso;
- **categoria não se apaga**: sumiu da origem, vira `ausente_na_origem` — pode
  haver de-para apontando para ela. E **ausência de destino é informação**, não
  pendência: transferências e totalizadoras não têm conta de demonstrativo
  própria, e o destino **nunca** é inferido.

**A camada de organizações NÃO foi uma sprint do hub.** Veio do épico ClickUp
`86e36ec0q` (16–18/09/2026, 8 tasks em três ondas, plano em
[Docs/PLANO_ORGANIZACOES.md](Docs/PLANO_ORGANIZACOES.md)) e deixou: tabela
`organizations`, `organization_id` em `clients`/`users`/`client_categories`,
`UserScope.PLATFORM`/`UserRole.PLATFORM_ADMIN`, `reach_filter`/`scoped_by_reach`,
`resolve_organization_for_creation`/`resolve_organization_filter`,
`modules/organizations/` e `scripts/promote_platform_admin.py`.

**A landing pública e a captação de leads também NÃO foram sprint do hub.** Vieram do
épico ClickUp `86e3fr9tj` (29/09/2026, plano em
[Docs/landing/PLANO_LANDING_PAGE.md](Docs/landing/PLANO_LANDING_PAGE.md), copy com fontes
em [Docs/landing/COPY.md](Docs/landing/COPY.md)) e deixaram: a raiz `/` como página
pública (grupo `app/(public)/`, com `/privacidade`), a tabela `leads`, o
`POST /api/v1/leads` público (honeypot, 3 por e-mail em 24 h, `slowapi` como teto global,
aviso no Slack fail-soft por `LEADS_SLACK_WEBHOOK_URL`, que não é canal de plantão), o
evento `lead_recebido` e as constantes de marca `lib/brand.ts`/`core/branding.py`. Em
30/09 o produto ganhou nome, **Hologram OS**, e a landing passou a nomeá-lo (86e3gr6k5,
junto com o refino visual). A landing oferece o manual em PDF para baixar. O domínio segue SEM DECISÃO — `hologramos.com.br` é só a primeira opção, ao lado de `hgos.com.br` e `holos.app.br` (86e3fr9wm,
roteiro em [Docs/landing/DOMINIO_E_NOME.md](Docs/landing/DOMINIO_E_NOME.md)): o
mapeamento de domínio do Cloud Run não atende `southamerica-east1`, então o caminho é Load
Balancer HTTPS.

---

## 9. Comandos Frequentes

> **Os comandos do portão de qualidade e os do dev local (`infra:up`, `db:migrate`,
> `db:seed`, `dev:api`, `dev:web`) estão na skill `gate`.** Migration tem roteiro
> próprio na skill `migration`; sprint do agents-hub, na `sprint-preflight`.

Preferir os **scripts pnpm da raiz** (ver `package.json`). Há um `Makefile`, mas os
targets do hub vêm de um `GNUmakefile` local e não-rastreado — ver `sprint-preflight`.

---

## 10. Pontos em Aberto (não decidir sozinho)

Quando o usuário não tiver decidido, **pergunte** antes de presumir:

**Decididos:**

- [x] ~~Framework Python~~ → **FastAPI** _(24/04/2026)_
- [x] ~~Job runner~~ → **`BackgroundTasks` do FastAPI** _(FASE 0, 16/06/2026 — ARQ/Redis removido por overengineering; antes era ARQ)_
- [x] ~~PM Python~~ → **uv** | ~~PM Frontend~~ → **pnpm** _(24/04/2026)_
- [x] ~~Monorepo vs polyrepo~~ → **Monorepo simples** _(24/04/2026)_
- [x] ~~Ambiente de staging/deploy~~ → **Google Cloud Run** (GCP `liberdade-assessoria`, `southamerica-east1`); dev no ar.
- [x] ~~Credenciais Omie sandbox~~ → **não existe sandbox no Omie.** Testes contra conta real (Quial), localmente; **nunca** comitar credenciais; rotacionar se vazar.

**Ainda em aberto (aguardando stakeholder / a confirmar):**

- [ ] Chave Anthropic com budget de longo prazo (parsing roda em dev, mas confirmar limite). _S9_
- [ ] Paginação de `ListarExtrato` (doc Omie incompleta — validar com Galhardo). _S5_
- [ ] `ListarContasPagar.filtrar_por_status` aceita múltiplos valores? _S5_
- [ ] Endpoint Omie que expõe saldo em data específica (fallback de `balance_start`). _S10_
- [ ] Política de senhas (rotação, complexidade). _S4_
- [ ] Ambiente de **produção** (o de dev já roda no Cloud Run; falta promover/configurar prod). _S18_

**Novos (do PRD FASE 0–5 — detalhe em [Docs/PLANO_PROXIMOS_PASSOS.md](Docs/PLANO_PROXIMOS_PASSOS.md)):**

- [x] ~~**Tolerância de data zero** também para conta corrente~~ → **SIM, aprovado + implementado** (FASE 1 / BACK 1.6): exato → `conciliado`; 1–3 dias → `conciliado_data_divergente` + `wrong_date`; > 3 → `sem_omie`. Vale p/ CC e cartão (`DATE_DIVERGENCE_RANGE=3` fixo). Na branch de integração; muda o comportamento da CC em prod quando a FASE 1 for mergeada. Ver §5.2.
- [x] ~~**Quebra do invariante "Omie read-only"**~~ → **implementado na Sprint 7**, com
      **`IncluirLancCC`** (lançamento na própria conta do cartão) e **não**
      `IncluirContaPagar`. ✅ **A captura S-1 foi feita em 21/08/2026** (conta Hologram,
      cartão Inter): contrato aninhado verificado, idempotência de `cCodIntLanc`
      confirmada (2º POST devolve o mesmo `nCodLanc`), fixtures anonimizadas no repo e
      gate rodando verde. Ver **§3.16**. ⚠️ **O que ficou em aberto:**
      (a) **representação de ESTORNO** (crédito) na escrita — hoje bloqueado
      (`estorno_nao_verificado`); precisa de captura própria (categoria de receita?
      valor com sinal?); (b) **reenvio pós-timeout** — o `ListarExtrato` não devolve
      `cCodIntLanc`, então a reconciliação atual é sempre inconclusiva; a idempotência
      provada permitiria reenviar com segurança — decidir com o Pedro; (c) ligar
      `OMIE_POSTING_ENABLED` em dev **só depois** deste código deployado. _FASE 2 / S24_
- [ ] **Cloud Run `--no-cpu-throttling` + `min-instances ≥ 1`** na API após remover Redis (senão BackgroundTasks congela). Custo aceitável? _FASE 0 / S20_
- [ ] **Pluggy interna vs Cubos** (proposta Arthur Souza, 16/06) + cobertura de Sicredi/BNB/Cora + primeiro endpoint público (webhook). _FASE 4_
- [ ] **Campo de departamento/rateio** na response Omie (bloqueia check `sem_departamento`); Slack (app vs webhook) e provedor de email; persona supervisor (role nova vs reuso). _FASE 5_

---

## 11. Estilo de Trabalho Preferido

- **Sessões focadas:** implementar uma sessão (S0, S1, ...) por vez, não pular.
- **Qualidade > velocidade:** seguir os melhores padrões de mercado, mesmo que demore mais.
- **Reuso obrigatório:** tudo que pode ser abstraído, deve ser. Sem duplicação.
- **Sem código mal feito:** prefira interromper e perguntar a entregar algo frágil.
- **Segurança é inegociável:** nunca corte caminho em segurança.
- **Escalabilidade é considerada:** arquitetura horizontal-ready desde o MVP (ver §6 do PLANO).
- **Nada "temporário":** se é pra ficar, faça direito desde o começo. Se é debug, tire antes do commit.

---

## 12. Comunicação ao Final de Tarefa

Toda vez que Claude termina uma tarefa, a resposta final **DEVE** ter duas partes, nesta
ordem: **(1) resumo executivo** — o que mudou, com arquivos, tamanho do diff, hash do
commit e status do gate, sempre com números reais; **(2) passo a passo de teste** — como
o usuário valida à mão, com comandos exatos, o estado esperado em cada passo, o caminho
feliz **e** pelo menos um caminho de erro. O que não pôde ser testado é dito
explicitamente, com o motivo.

> **O roteiro completo de fechamento** (branch, commit local em Conventional Commits,
> nada publicado, e os comandos de push e PR entregues prontos) está na skill
> **`entrega`**.

Evite "você já sabe" — o usuário pode voltar à entrega depois de dias.

---

## 13. Atualização deste Arquivo

**Manter este arquivo atualizado é obrigação contínua — parte do _Definition of Done_, não um extra.** Sempre que uma tarefa disparar um dos gatilhos de _"Quando atualizar"_ abaixo, atualize o CLAUDE.md **na mesma entrega** (mesmo PR/commit que fez a mudança) e ajuste o rodapé de versão. Não acumule "atualizo depois": primer desatualizado induz erro nas próximas conversas e custa mais caro que um parágrafo a mais. Na dúvida se algo se qualifica, trate como gatilho — ou **pergunte** (§6, §10). Esse passo conversa com a §12: o fim de tarefa é o momento natural de revisar se o primer precisa mudar.

**Quando atualizar:**

- Decisão arquitetural tomada (confirmar framework, job runner, etc.).
- Mudança em regra de negócio crítica.
- Novo padrão adotado que vale para o projeto inteiro.
- Descoberta que contradiz a documentação original (registrar delta).

**Quando NÃO atualizar:**

- Detalhes de implementação de uma feature específica (isso vai em PR + comentários no código).
- Status de progresso — use o backlog, não o CLAUDE.md.
- Lições aprendidas pontuais (vão em runbooks em `Docs/runbook.md` quando S18 chegar).

**Como atualizar:**

- Edit direto, sem seções `# Removed` ou comentários "// antes era X". Trate este arquivo como lei atual, não como histórico.
- Mantenha cada seção sob 400 linhas. Se crescer demais, extraia para `Docs/` e linke daqui.

---

_Versão 1.83, 08/10/2026. **A identidade entrou no app autenticado sem virar enfeite: primitivos compartilhados com a landing, movimento discreto, estados vazios com vinheta (86e3h579d, 86e3h57a5, 86e3h57b5 e 86e3h57ch, subtasks 3 a 6 do épico 86e3h56nk), e dois acabamentos do menu e do painel sem task.** Nasceram `Card variant="elevated"` (o `.lp-card` em tokens, por `--card-*`), `AnimatedCheck` (o `.lp-check`) e `Reveal`/`useReveal`/`observeReveal` (a revelação na rolagem); a landing os consome e ficou igual (as cores dela entram pelas variáveis `--card-*` do `public-brand.css`; dos 20 prints da landing e do login, 19 saíram idênticos pixel a pixel ao da base e o 20º difere só na faixa do header rolado, por timing de captura; no toque o card não sobe mais, por regra). No app: cards de totais da carteira, do de-para e do plano de contas e os blocos do painel elevados e com entrada única, item ativo do menu com barra de 3 px em `--primary` e brilho fora do texto, filete de 1 px sob o header (`.header-filament`) e o check que se desenha no toast de sucesso. A entrada é por ANIMAÇÃO (e não transição) para não brigar com o hover do card no mesmo elemento. Cinco vinhetas (`shared/vignettes.tsx`, até 20 elementos, cor por classe de token) no `EmptyState` novo; os vazios de origens e de clientes saíram da célula `colSpan` para o `TableEmpty`. O gate de a11y passou a rodar com movimento reduzido por padrão e ganhou dois cenários com movimento (painel e menu, contraste por pixel depois da animação, filete fora do texto). Sem task (decisão do Pedro): o contador do De-para no menu é o MAIOR "sem decisão" entre destinos (265, não 1325 = 265 × 5), com tooltip de detalhe nos três contadores; o `<main>` e a raiz do `FlowChart` são `relative` (o `sr-only` da tabela do fluxo esticava o documento para 1137px numa janela de 864) e o painel declara `data-page-scroll` (o último card colava na borda). Lista canônica (**120**), matriz (**29**) e pares de AAD (**17**) não mudaram._

_Versão 1.82, 08/10/2026. **A lista de conciliações volta a ser a entrada do cliente e o painel repaginado fica em `/painel` (decisão do Pedro depois do uso com dado real; acabamento dos PRs #297 e #299, sem task).** `/clientes/{id}` é a lista, `/painel` o painel, `/conciliacoes` virou 308 para a raiz levando a query, e o menu segue casando cada item pela própria rota. A gestão de origens saiu do painel para o topo de Contas Bancárias (`originFixPath()` aponta para lá e o `?conectar=` abre a gaveta onde a seção estiver); no painel ficou uma linha de estado na faixa "Atividade", e a seção inteira só aparece, antes do fechamento, quando não há origem ativa. Com uma seção acima da tabela, Contas Bancárias passou ao padrão em que a página rola. O fluxo previsto ocupa a largura inteira, "Atividade" virou faixa de três colunas e o eixo escreve "R$ 1,5 mi" a partir de um milhão. O card "Conciliações do mês" deixou de medir contra o cache de contas (que traz caixinha, adiantamento, reembolso e cartões que ninguém concilia todo mês) e passou às **contas habituais**: `habitualAccountIds` no `/summary`, contas com sessão ativa no mês ou nos 3 anteriores (`HABITUAL_MONTHS`, num lugar só), calculadas no servidor; as demais contas ficam recolhidas. O contraste por pixel da landing no e2e passou a garantir o alvo abaixo do header fixo antes da foto (o flake de 2,85:1), sem mexer no limite de 4,5. Lista canônica (**120**), matriz e pares de AAD não mudaram._

_Versão 1.81, 07/10/2026. **O painel virou a tela de entrada do cliente, com os contadores no menu e o fluxo previsto da carteira (86e3k1q3x, 86e3k1q4g, 86e3k1q54 e 86e3k1q5n, subtasks 4 a 7 do épico 86e3k1q1u; a 7 decidida pelo Pedro em 07/10).** `/clientes/{id}` renderiza o painel, a lista de conciliações foi para `/clientes/{id}/conciliacoes` e o `/painel` antigo é um 308 do `redirects()` do `next.config.mjs`, levando a query (um `permanentRedirect()` na página respondia 200 e redirecionava no navegador com refresh de 1 s) (o `?conectar=<tipo>` da gaveta de conexão nasceu apontando para ele); `homePathFor` e o `middleware.ts` não mudaram, e o item da casa do tenant na camada global passou a se chamar "Painel". No menu do cliente o "ativo por exclusão" acabou: cada item casa pela própria rota (Conciliações na lista, no detalhe e no processamento), e rota sem dono não acende nada. O menu lê o resumo do cliente e mostra três contadores, cada um no tom do badge da tela de destino: Conciliações em `info` (processando + em revisão), Carteira em `destructive` (títulos vencidos, ausente com `titles` nulo) e De-para em `warning` (sem decisão somado nos destinos); zero, carregando e erro ficam sem pílula, e as mutações que mudam as contagens invalidam o resumo (sem polling). Nasceu `GET /clients/{id}/titles/flow`: seis faixas fechadas por vencimento contra o "hoje" do servidor (`vencidos`, `ate_7`, `8_30`, `31_60`, `61_90`, `90_mais`, limites em `client_titles/flow.py`), a receber e a pagar com o líquido, numa query agregada com `client_id` e `scoped_by_tenant`; `vencidos` é a régua do aging (`due_date < hoje`), então as faixas somam o em aberto do `/summary` e `vencidos` é o vencido, provado contra o banco. O painel (`features/clients/dashboard/`) não calcula número no navegador: fechamento do mês (contas concluídas por conta do cache, anomalias com nome do catálogo, compras do cartão, de-para com "—" na cobertura nula), carteira com a barra de atraso e os rodapés de inadimplência e do vence-em-7-dias, fluxo previsto em SVG inline (cor por `style="fill: hsl(var(--token))"`, `role="img"` + tabela `sr-only`, vencidos fora das barras e o rótulo "não é saldo de conta") e origem e atividade; cada bloco carrega e falha sozinho, e um 403 da carteira esconde só carteira e fluxo. "Nova conciliação" usa a MESMA gaveta e a MESMA decisão da lista (`useCreateReconciliationDrawer`, `reconciliationCreation`). O "Resumo geral" do cabeçalho da conciliação passou a escrever a Diferença com `<Money tone="sign">`. O print de 390px achou dois defeitos que o axe não vê: a `<table className="sr-only">` ignorava o `width: 1px` (tabela cresce até o conteúdo) e empurrava a página para 422px, e os cinco líquidos sob as barras encostavam um no outro; o `sr-only` foi para um `div` e, abaixo de `sm`, o líquido vira lista. Lista canônica 119 → **120**, matriz (**29**) e pares de AAD (**17**) não mudaram._

_Versão 1.80, 07/10/2026. **O menu do cliente virou três seções, o dinheiro com sinal ganhou um componente só, e nasceu o resumo do cliente (86e3k1q2j, 86e3k1q30 e 86e3k1q3j, subtasks 1 a 3 do épico 86e3k1q1u).** O menu do cliente (`clientNavSections`, em `nav-items.tsx`) devolve `NavSection[]` em Operação (Painel, Conciliações, Carteira, De-para, Origem por arquivo), Cadastros (Contas Bancárias, Glossário, Plano de Contas, Plano contábil) e Acesso (Usuários), renderizadas pelo MESMO `NavSections` da camada global; seção sem item some inteira (o operador não vê Acesso), o gating de cada item e a rota de entrada não mudaram (a lista de conciliações continua em `/clientes/{id}`, decisão da subtask 7) e "Conciliações" segue ativo por exclusão. O `NavItem` ganhou `count`, `countTone` e `countLabel`, ainda sem uso: o `NavLink` só desenha a pílula com o nome acessível presente (o número sozinho não diz do que é). Nasceu `components/shared/money.tsx` (`<Money>`, `moneyToneClass`, `formatMoney`): `tone="sign"` escreve `+` e o menos tipográfico U+2212 sobre `formatBRL(|valor|)`, zero no centavo sem sinal nem cor; `overdue`/`warning` colorem pela situação; `formatBRL` não mudou. Migraram o "Vencido" e os baldes da carteira, o total do relatório de recebíveis, a "Diferença" da aba Resumo e o valor da gaveta de lançamento no Omie (os dois últimos passaram a mostrar o sinal e a cor). O cenário e2e novo da Diferença (saldo divergente, que nenhum cenário montava) achou um defeito ANTERIOR: a nota da "Diferença" e o `hint` do `Indicator` eram `<p>` dentro do grupo do `<dl>` (axe `definition-list`, serious); viraram um segundo `<dd>`. Os pares `success`, `destructive` e `warning` sobre `background` e `card` entraram no `theme-contrast.test.ts`; o mais justo é `destructive` sobre `card` no Hologram, 5,00:1. `GET /clients/{id}/summary` (módulo `client_summary/`, `AccessibleClientDep`, `?month=YYYY-MM` validado por `COMPETENCE_PATTERN`) devolve só contagens: conciliações do mês por status e contas cobertas, anomalias abertas por código do tipo e resolvidas, compras de cartão elegíveis ao lançamento (`sem_omie`, sem vínculo, valor negativo, sem posting `confirmed`), categorias sem decisão e cobertura por destino ativo, se o destino está materializado, títulos vencidos (`null` sem `view_client_receivables`) e a conciliação mais recente. O de-para não tem segunda implementação: `listing.py` ganhou `universe_keys` e `situation_counts` (a mesma vigência e o mesmo universo da lista, sem decifrar histórico), e a cobertura é a `apply_mapping` sobre Σ|valor| por categoria somado no banco, com uma casa. Lista canônica 118 → **119**, matriz (**29**) e pares de AAD (**17**) não mudaram._

_Versão 1.79, 07/10/2026. **No tema Hologram o verde da marca virou a cor de AÇÃO do app autenticado, e a logomark ganhou token próprio para continuar branca (86e3h5783 e 86e3h578n, subtasks 1 e 2 do épico 86e3h56nk).** Nasceram três tokens nos três blocos do `globals.css`, expostos no Tailwind: `--logo` (cópia literal do `--primary` no claro e no escuro, `0 0% 98%` no Hologram; toda `BrandMark` que usava `text-primary` passou a `text-logo`), `--link` (o `--foreground` no claro e no escuro, o verde no Hologram; aplicado nos dois links de ação em texto do app, `edit-client-modal` e `origin-state-notice`, e na variante `link` do `Button`, hoje sem uso) e `--primary-hover` (o hover do `Button` e do `Badge` `default` deixou de ser `primary/90` e `/80`: no claro `240 49.2% 28%` e no escuro `240 54.4% 65.2%`, o que o `/90` dava sobre o fundo, travado a 1 unidade por canal; no Hologram `175 95% 38%`, o `--brand-hover`). No bloco `.hologram`, `--primary` e `--ring` passaram ao verde e `--primary-foreground` ao navy; `--accent` ficou. Contrastes medidos no `theme-contrast.test.ts`: navy sobre o verde 8,94:1 e sobre o hover 7,31:1; anel e link verdes 9,70:1 sobre o fundo e 9,32:1 sobre o card; logomark 17,27:1 (claro), 6,04:1 (escuro) e 17,24:1 (Hologram, sobre o card); rótulo do primário em hover 13,04:1 (claro) e 4,89:1 (escuro); o chip "Destaque" composto, verde no Hologram por decisão do Pedro, 7,83:1 no card e 7,31:1 na linha em hover; branco sobre o verde continua reprovando (1,85:1). A `public-brand.css` lia `--primary` para o brilho da aurora e as bordas dos cards (branco no Hologram fixo): passou a ler `--foreground`, mesmo `0 0% 98%`, e a landing e o login não mudaram. Achado no caminho: o `bg-primary/10 hover:bg-primary/15` da linha selecionada do "Trocar lançamento" nunca pintava, porque o `data-[state=selected]:bg-muted` do `TableRow` vence pela especificidade do seletor de atributo; saiu como código morto (com ele, o fornecedor em cinza mediria 4,38:1 no claro). Nenhum backend: endpoints sensíveis (**118**), matriz (**29**) e pares de AAD (**17**) não mudaram._

_Versão 1.78, 06/10/2026. **O limite do login passou a ser por identidade, e o limite por IP virou teto de enxurrada (86e3anx10, a task urgente do épico 86e3anwzu).** Atrás do BFF do Next a API vê o IP do proxy para todo mundo, e o `5/5minutes` por IP do slowapi era um balde só para a plataforma inteira por instância: cinco erros de digitação de pessoas diferentes travavam o login de todos, e qualquer um derrubava o login de propósito. Nasceu `LoginIdentityLimiter` em `core/rate_limit.py` (`MovingWindowRateLimiter` da `limits` 5.8.0 sobre `MemoryStorage`, `5/5minutes` por chave `login:<sha256 do e-mail normalizado>`), consultado pela rota `login` com o `payload` já validado e ANTES do `AuthService.login` (`test`, que não consome cota) e alimentado só no `except UnauthorizedError` (`hit`); sem middleware que pré-lê o body, e o `TODO S16` que o previa saiu. O 429 por identidade tem mensagem própria com a janela real ("Aguarde 5 minutos") e loga só `window` e um `identity_prefix` de 8 hex. O decorador por IP passou a `LOGIN_FLOOD_LIMIT = "60/minute"`, no molde do `LEADS_RATE_LIMIT`. A tela de login deixou de ter texto próprio para 429 (dizia "1 minuto") e mostra o `userMessage` do servidor. Nova regra §3.17, com `--forwarded-allow-ips` proibido enquanto a API for pública. Endpoints sensíveis (**118**), matriz (**29**) e pares de AAD (**17**) não mudaram; o contrato mudou só em texto de descrição (o do login e um resto não regenerado da 86e3anx4u). Storage compartilhado e IP real ficam pendentes (86e3anx7y item 3, 86e3anx69)._

_Versão 1.77, 06/10/2026. **Nasceu a revogação de sessão sem desativar a conta nem trocar a senha (86e3anx4u, parte 1 do item do épico 86e3anwzu).** Até aqui, para tirar uma pessoa de todos os dispositivos sem matar a conta, só existia redefinir a senha dela pela plataforma. Agora quem GERE o usuário encerra as sessões dele: `POST /users/{id}/sessions/revoke` (staff, `ManageOrgUsersDep`, alvo pelo recorte escopado do módulo mais, só para a plataforma, os pares de plataforma, porque a lista de administradores da plataforma oferece a ação) e `POST /clients/{id}/users/{user_id}/sessions/revoke` (usuário do cliente, `ManageClientUsersDep` sobre `AccessibleClientDep` e `OpenClientDep`, alvo com `AND client_id` no SELECT). Opção A da task, alinhada com o Pedro em 06/10: o mecanismo é o MESMO carimbo `users.password_changed_at` e o MESMO check `token_predates_password_change` da 86e3ewukz, sem migration, sem coluna nova e sem tabela de `jti`; a §3.12 passou a dizer que o carimbo tem dois gatilhos. Nenhuma permissão nova (§4.9 explica por quê): a matriz segue com 29 células. Lista canônica 116 para **118**, com os três atacantes da bateria. Evento `sessoes_encerradas` só com IDs, sem dedup. Na tela, a ação "Encerrar sessões" (ícone de saída) entrou nas três listas, decidida por `hasPermission` e nunca na própria linha, com `AlertDialog` sem formulário que diz que a conta continua ativa e a pessoa entra com a senha atual. Dois detalhes que valem fora da task: o `AlertDialogAction` do Radix fecha no clique, então ação assíncrona que quer ficar aberta até o sucesso faz `event.preventDefault()` no `onClick`; e o 403 do guard de tenant para admin de OUTRA organização na rota do cliente é o mesmo de toda escrita da família `/clients/{id}/users` (não nasceu uma conversão nova para 404). A detecção de reuso de refresh rotacionado é a parte 2, task própria do Pedro._

_Versão 1.76 — 05/10/2026. **O redator de log passou a mascarar também pela FORMA do valor, e o `'unsafe-eval'` saiu da CSP de produção (86e3anx7y, itens 5 e 1 da dívida menor de hardening do épico 86e3anwzu).** Item 5: até aqui a decisão era 100% pelo nome da chave, então segredo sob chave inocente (`error=str(exc)` com a URL do webhook dentro) passava direto, e o redator rodava ANTES do `format_exc_info`, de modo que o texto da exceção nunca era varrido. Agora uma segunda camada varre toda string do evento e troca só o trecho casado por `[REDACTED]` (§3.3), com uma constante nomeada por formato e a exceção do hash, que é a lição do `input_tokens` aplicada ao `file_hash`. O processor passou para depois de `StackInfoRenderer` e `format_exc_info` (`build_processors`), com teste da cadeia de produção inteira, renderer JSON incluído. A primeira versão do padrão do Discord tinha `(?:[a-z]+\.)?` no início e levava **74 s** para varrer 256 KB de letras sem espaço; com os subdomínios literais, 0,04 s, e a pior entrada adversarial medida de 256 KB leva cerca de 0,04 s. Acima de 256 KB a cauda vira `[REDACTED]` sem ser lida (fail-closed, como o teto de profundidade). Na suíte de hoje nenhum log carrega o `file_hash` inteiro (a conciliação loga `hash_prefix` de 8); a exceção protege o que vier. Item 1: `'unsafe-eval'` só entra no `script-src` fora de produção (o `next dev` precisa dele para HMR e source map); o `'unsafe-inline'` segue como dívida P2. O gate de a11y ganhou um cenário que lê o header da landing e de uma página autenticada, um coletor em TODO cenário que reprova se o browser acusar violação de CSP, e um sentinela que prova as duas pontas: um script da página tenta `new Function`, recebe `EvalError` e o coletor registra. Duas armadilhas medidas no caminho: o `page.evaluate` do Playwright é ISENTO de CSP (o `Runtime.evaluate` libera `eval` enquanto dura, inclusive num `<script>` inserido dentro dele), então o sentinela roda o eval num `setTimeout` do script da página; e o eval recusado e capturado não gera linha de console, só o evento `securitypolicyviolation`, que um init script repassa. O coletor tem UMA exceção, do ambiente: no standalone local o redirect do `middleware.ts` sai para `https://localhost:<porta>` (`request.nextUrl` mais o `upgrade-insecure-requests`), origem diferente da do gate, e o prefetch de RSC que cai nele leva bloqueio de `connect-src`; em dev o host é o do serviço. A API não emite CSP própria. Os itens 2 (política de senha), 3 (storage do rate limit) e 4 (lockout por conta) ficaram para decisão do Pedro. Endpoints sensíveis (**116**), matriz (**29**) e pares de AAD (**17**) não mudaram._

_Versão 1.75 — 05/10/2026. **Nasceu a lista de subprocessadores, e o manual passou a dizer o que os termos da Anthropic sustentam (86e3anx75, épico 86e3anwzu de hardening pré-piloto).** A extração manda o arquivo inteiro para a API do Claude e a qualificação manda descrição, valor, fornecedor e categoria de cada movimentação conciliada, mais o glossário decifrado: existe um subprocessador, e "os dados passam pela nossa estrutura" estava incompleto. `Docs/seguranca/SUBPROCESSADORES.md` lista Anthropic, Google Cloud, Omie e Slack (o aviso de lead leva nome, e-mail, empresa e WhatsApp) com o que vai para cada um, arquivo e linha que provam, e a página oficial com data de acesso; e a seção "O que não sai da plataforma", com o arquivo que prova cada garantia. Dos termos, conferidos em 05/10/2026: a API comercial não treina com o conteúdo do cliente; entrada e saída são apagadas em até 30 dias, com exceções (conteúdo sinalizado por violação da política de uso fica até 2 anos); há retenção zero por organização, a pedido; o dado em repouso fica só nos Estados Unidos e a inferência padrão pode rodar em qualquer geografia, sem região na América do Sul, o que para a LGPD é transferência internacional. Ficaram **a confirmar pelo Pedro**: retenção zero e geografia de inferência no console da Anthropic, região do Cloud SQL e servidor SMTP do alerta. **Delta com a §2:** Sentry e Grafana/Loki não recebem dado hoje; o `sentry-sdk` é dependência e `SENTRY_DSN` existe, mas nada chama `sentry_sdk.init`, e o log vai para stdout, no Cloud Logging. O manual mudou só na fonte HTML; o PDF e a cópia da landing ficaram para regerar. Nenhum código de produto mudou: endpoints sensíveis (**116**), matriz (**29**) e pares de AAD (**17**) iguais._

_Versão 1.74 — 02/10/2026. **O PDF grande deixou de ir inteiro para a IA: é dividido por PÁGINAS e extraído em blocos paralelos (86e3ff8xd, épico 86e3ff8w3; cinco envios do extrato BB do Laticínio em 28/09 estouraram o teto aos 150 s sem nenhum `parse_chunked`).** A extração em blocos da 86e39xvxm cobria só CSV e XLSX. As sete decisões da task, todas mantidas: **D1** cortar por página com o `pypdf` (já instalado, sem uso) e reusar `_extract_in_blocks` + `merge_statements`, em vez de subir o teto (arrasta `proxyTimeout` e Cloud Run) ou de streaming (o tempo total não muda e o BFF corta aos 160 s); **D2** uma chamada CURTA só com a página 1, tool nova `identify_document` (`max_tokens` 256, system prompt próprio), devolve `{bank_name, account_type}` que entra como nota no user prompt de TODO bloco, porque página do meio não tem cabeçalho e as regras 9 a 14 dependem do tipo de conta; depois todos os blocos, inclusive o primeiro, em paralelo no MESMO semáforo das `ADL_PARSE_CHUNK_\*`; **D3** `ADL*PARSE_PDF_PAGES_PER_BLOCK=2`, `ADL_PARSE_PDF_MIN_PAGES=4`(até aí vai inteiro, sem identificação) e`ADL_PARSE_PDF_MAX_PAGES=30`, acima disso o `/parse`recusa com 400 acionável ("Envie em partes de até 30 páginas e anexe todas na mesma conciliação",`details={pages, maxPages}`) sem chamar a IA, porque o orçamento de parede é fixo; **D4** bloco pode vir SEM movimentação (`ExtractedStatementBlock`, `min_length=0`, só em modo bloco) e a nota de bloco manda devolver lista vazia em vez de inventar; a junção segue exigindo ≥ 1 no total e o período ignora bloco vazio; **D5** com `identity`, banco e tipo vêm dela e bloco divergente NÃO derruba o arquivo (só `parse_pdf_block_divergence`com números), sem`identity`o CSV/XLSX fica idêntico; **D6** o`pypdf` nunca bloqueia: ilegível, cifrado sem senha vazia ou poucas páginas vai inteiro como antes (`parse_pdf_split_skipped reason=unreadable|encrypted|too_few_pages`), e os quatro cenários de integração com o PDF falso ficaram intactos; **D7** todo `/parse`termina com`parse_completed` (`file_type`, `bytes_in`, `split`, `blocks`, `pages`ou`data_records`, `transaction_count`, `duration_ms`) e `parse_chunked`ganhou`file_type`e`pages`, só contadores. A tool `extract_movements`e o`SYSTEM_PROMPT` não mudaram byte a byte (prompt caching); as duas chamadas passam pelo MESMO retry e mapeamento de erro (`\_call_with_retry`). O planner (`parse_pdf_pages.py`) roda em `run_in_threadpool`. Nenhum endpoint, permissão nem campo cifrado novo: endpoints sensíveis (**116**), matriz (**29**) e pares de AAD (**17**) não mudaram. **O que NÃO foi medido:** a chamada real à Anthropic com o PDF do Laticínio é do Pedro; o `duration_ms`de`parse_chunked file_type=pdf`é o que calibra`ADL_PARSE_PDF_PAGES_PER_BLOCK`.*

_Versão 1.73 — 02/10/2026. **Dois rodapés diziam v1.69 e a numeração foi corrigida (as tasks 86e3gkd4y e 86e3gkd80 do épico 86e3gkd0a numeraram em paralelo, cada uma no próprio worktree):** a nota do `data-page-scroll` (86e3gkd80) virou **v1.71** e subiu para a posição cronológica (o PR #277 entrou depois do #275), e a do export do Domínio (86e3gkd7y) virou **v1.72**. Lição para entrega paralela: o número do rodapé se decide no MERGE, não no worktree — quem mescla por último confere a sequência. De carona, o produto ganhou favicon: `apps/web/src/app/icon.png` e `apple-icon.png` (o H branco da logomark sobre o marinho `#0C0C5A`, quadrado arredondado), servidos pelo Next pela convenção de arquivo do App Router, sem mudança de layout — a aba do browser deixa de mostrar o globo genérico._

_Versão 1.72 — 02/10/2026. **A importação do plano contábil aceita o plano EXPORTADO do Domínio (.xlsx), e a decisão da v1.58 ("o export nativo fica para um futuro talvez, por não ter colunas documentadas") foi revista porque a amostra chegou (86e3gkd7y, épico 86e3gkd0a).** Sem rota, permissão nem coluna nova: o `POST …/accounting-chart/import` detecta o layout, só em XLSX, por uma linha das 10 primeiras com exatamente `Código`/`T`/`Classificação`/`Nome`/`Grau` (aparadas, sem caixa e sem acento); sem ela, o caminho do modelo é o de sempre, e o que não é nenhum dos dois recebe o `CABECALHO_DIVERGENTE` de hoje. O export tem banner, 2.407 mesclas, cabeçalho desalinhado do dado e nome indentado pela hierarquia, então a leitura é POR PADRÃO DE CÉLULA (`código` · `S` opcional · `classificação` · `nome` · `grau`), nunca por letra de coluna, e as linhas convertidas passam pelo MESMO `validate_rows` do modelo. O tipo vem da marca `S`, nunca de ter filhos (40 grupos sintéticos vazios na amostra). Na dúvida recusa: grau diferente da profundidade da classificação, classificação repetida, marca de tipo estranha e linha com cara de conta DEPOIS do fim do bloco (o rodapé com assinaturas e CPF fica fora sem ser interpretado) são `LINHAS_INVALIDAS` com sete motivos novos de vocabulário fechado, sem célula na resposta. O leitor cru (`read_xlsx_raw_rows`) mora no `reader.py` da S14, com os mesmos guardas. A amostra real (com PII) NÃO entra no repo: a fixture `tests/fixtures/accounting_chart_dominio/` é gerada por `scripts/anonymize_dominio_chart_sample.py`, que troca nome de conta, banner, rodapé e metadados, preserva a estrutura (mesclas e posições idênticas) e confere no fim que nenhum dos 532 textos da amostra sobrou no XML; o teste-ouro compara as 563 contas com `plano_esperado.csv`. `plano_contabil_importado` ganhou a quinta chave `layout` ∈ {`modelo`, `dominio`}. Na tela, o "Modelo da planilha" (gaveta e estado vazio) ganhou uma linha dizendo que o export do Domínio em .xlsx é aceito, os sete motivos têm rótulo, e o rodapé da recusa por linha deixou de dizer "A linha 1 é o cabeçalho" (no Domínio o cabeçalho é a linha 5; o print da recusa pela API real é que mostrou isso). Provado de ponta a ponta contra a API real local: a fixture e a amostra real entram com 563 contas e 143 sintéticas, reimportar é idempotente, o arquivo irreconhecível recebe a recusa de hoje e o adulterado é recusado sem tocar o plano existente. Risco declarado: uma amostra só; amostra nova vira fixture nova antes de a leitura afrouxar. Endpoints sensíveis (**116**), matriz (**29**) e pares de AAD (**17**) não mudaram._

_Versão 1.71 — 01/10/2026. **A tela em que a PÁGINA rola agora declara isso, e o `ClientShell` solta a altura fixa para ela (86e3gkd80, print da demo de 29/09: "não tem scroll, mas não dá pra ver a linha final da tabela").** O diagnóstico óbvio (aplicar o padrão da carteira) já estava descartado: a tela "Plano contábil" usava `<TableCard pageScroll>` desde 28/09. Medido no browser, o `<main>` ROLAVA (`scrollHeight` 1025 contra 835 no desktop, estado vazio), mas a rolagem parava com a borda inferior do card "Conta do banco" exatamente na borda da janela: o `ClientShell` dá à tela uma caixa de altura FIXA (`h-full` + `min-h-0 flex-1`, feita para o padrão FILL), a tela pageScroll a transborda, o transbordo de descendente entra na área rolável do `<main>`, mas o `padding-bottom` do `p-6` só é somado depois do filho em fluxo, que terminava 24px acima. As quatro telas pageScroll tinham o mesmo final (folga de 0 a 1px): no plano contábil o último elemento é um card com borda e cantos arredondados, então o defeito virou "card cortado"; nas outras três era só o respiro perdido abaixo da paginação. Correção, decidida com o Pedro entre três sinais possíveis: cada tela pageScroll (plano contábil, carteira, de-para, plano de contas) declara `data-page-scroll` na `<section>` raiz, e o shell responde por `has-[[data-page-scroll]]:h-auto`/`min-h-full` na raiz e `has-[[data-page-scroll]]:flex-none` no wrapper; as telas FILL não mudam. Ficaram de fora o `<TableCard pageScroll>` emitir o atributo sozinho (no estado de erro, sem tabela, a tela voltaria à cadeia fixa) e a uma lista de rotas no shell. A guarda `exigirFimDaPaginaComRespiro` mede a folga entre o alvo e o fundo do `<main>` rolado até o fim: reprovou as 20 combinações (5 cenários: carteira, de-para, plano de contas e o plano contábil vazio e com plano; × 2 viewports × 2 projetos) com as classes antigas e passa com as novas._

_Versão 1.70 — 01/10/2026. **O `.xls` legado voltou a aparecer no seletor de arquivo para receber a recusa acionável do servidor (86e3gkd50, épico 86e3gkd0a).** Na demo de 29/09 o plano de contas exportado do Domínio (`.xls`) nem aparecia no seletor das telas de importação: o `accept` o filtrava no navegador, uma recusa silenciosa. O backend já recusava o XLS pelos magic bytes (OLE/CFB) com 422 `FORMATO_NAO_SUPORTADO` e "Salve a planilha como XLSX ou CSV", e as telas já mostravam esse `userMessage`, mas o arquivo não chegava até lá: além do `accept`, o zod do envio do mês e o do plano contábil barravam a extensão antes de enviar. A lista do navegador virou `SELECTABLE_FILE_EXTENSIONS` (`.csv`, `.xlsx`, `.xls`), fonte do `FILE_ACCEPT` (com o MIME `application/vnd.ms-excel`) e da checagem do zod, então o par não diverge mais. Cobre o envio do arquivo do mês, o editor de mapeamento e a gaveta de importar plano contábil; o import de de-para segue só `.xlsx` (o arquivo é exportado pela própria plataforma). A frase do zod do envio perdeu o "e XLS" ("PDF não tem colunas para mapear"), porque o XLS não cai mais nesse ramo. O e2e ganhou o bloco ".xls legado" (o `accept` real, a recusa do servidor com a instrução e o axe nas três telas), e a recusa foi conferida contra a API real nas três rotas. Regra nova na §7 Frontend e na skill `front-gate`. Nada de backend; endpoints sensíveis (**116**), matriz (**29**) e pares de AAD (**17**) não mudaram._

_Versão 1.69 — 01/10/2026. **O campo de arquivo virou componente compartilhado com cara de botão (86e3gkd4y, épico 86e3gkd0a).** Na demo de 29/09 o "Escolher arquivo" das gavetas de importar plano contábil e importar de-para não parecia clicável: era o `<Input type="file">` cru, cujo botão nativo não muda o cursor nem reage ao hover. O `FileInputField`, que já existia na conciliação e não tinha consumidor (a gaveta usa um multi-arquivo inline), subiu para `components/shared/` e substituiu os quatro usos crus: as duas gavetas de importar, o envio da origem por arquivo e o editor de mapeamento, com o `accept` de cada tela intacto. Na promoção ele ganhou quatro coisas: `ref`/`name`/`onBlur` encaminhados ao input real (o RHF continua focando o campo com erro), o anel de foco no gatilho visível por `peer-focus-visible` (antes o foco de teclado caía num input invisível), o input nativo zerado quando o valor volta a `null` (reset da gaveta do de-para; sem isso o mesmo arquivo não dispararia `change`) e o fim do `title` nativo no nome truncado. O e2e mede nas quatro telas o cursor, o fundo que muda sob o ponteiro, o axe com o hover ativo e o "Remover" dentro da viewport em 390px. Regra nova na §7 Frontend e na skill `front-gate`. Nada de backend; endpoints sensíveis (**116**), matriz (**29**) e pares de AAD (**17**) não mudaram._

_Versão 1.68 — 30/09/2026. **O login virou a ponte entre a landing e o sistema, e o verde da Hologram virou token do tema (86e3h1h75, pedido do Pedro: "essa caixa de login está meio feia, estranha e desproporcional").** O verde saiu da `landing.css` (`--lp-brand`, 86e3h0xcr) para o `globals.css` como `--brand`, `--brand-foreground` (o navy, cópia literal do `--primary` claro) e `--brand-hover` (sólido: o verde com 10 % de preto convertido para HSL, pela regra do `--destructive-hover`), com o MESMO valor nos três blocos, e o `Button` ganhou `variant="brand"`; a classe `.lp-cta` e o `landing-contrast.test.ts` sumiram, e os pares foram para o `theme-contrast.test.ts` (navy sobre o verde e sobre o hover nos três temas, verde sobre fundo e card do Hologram, branco sobre o verde reprovando). O que a landing e o login dividem (variáveis `--lp-*`, aurora, grade, palavra em gradiente, borda em gradiente) foi extraído para `app/public-brand.css`, escopado em `.lp-public`. O login ganhou tema Hologram fixo no wrapper (o `<html>` continua com o tema salvo), duas colunas de `lg` para cima com o painel de marca (aurora a 55 %, logomark, a frase e os chips do hero), card de 384 px com logomark e título dentro, subtítulo "Entre com o seu acesso." e placeholder `voce@empresa.com.br` (a plataforma é multi-organização desde o épico 86e36ec0q), campos de 44 px, "Entrar" verde que desabilitado só apaga, erro só na mensagem com ícone (`FormMessage` aceita `icon`, opcional) e o rótulo na cor do texto, "Voltar para o site" e "Hologram Gestão". Os textos novos moram em `content.ts` (`login`); o fluxo de autenticação e o middleware não mudaram. Medido por pixel no Hologram: "Entrar" em hover 7,32:1 (o CTA da landing também, antes 7,24:1 pelo `color-mix`), frase do painel sobre a aurora 15,63:1 e os dois stops do destaque 17,07:1 e 11,31:1; os números da landing não mudaram. Os helpers de medição por pixel do e2e subiram do bloco da landing para o topo do spec, e o bloco novo "Login: a ponte entre a landing e o sistema" mede tema fixo, painel, card sem rolagem horizontal em 390px, hover do botão e rótulo sem vermelho._

_Versão 1.67 — 30/09/2026. **A landing ganhou o verde da Hologram e perdeu dois efeitos (86e3h0xcr, feedback de uma colega: "senti falta do verde da Hologram").** O verde `#05d1bf` (hsl 175 95% 42%) foi amostrado por pixel do botão e do título do site da Hologram (`Docs/brand/site-hologram-cta-2026-09-30.png`, 103.571 pixels exatos) e entrou como `--lp-brand`, variável escopada da landing com a fonte ao lado, no lugar do `--info` em todos os efeitos: aurora, stop claro do título (misturado 60/40 com `--foreground`, medido por pixel a 7,23:1 no desktop e 6,92:1 em 390px), ponto da pílula, linha e pastilha ativa do "Como funciona", filetes, borda em gradiente, sombras e o cadeado. O botão primário da landing (`.lp-cta`: header, hero e "Enviar") é verde com texto navy, `--lp-brand-fg`, cópia literal do `--primary` do tema claro, porque dentro da `.hologram` o `--primary` é branco e branco sobre o verde dá 1,85:1; navy sobre o verde dá 8,94:1 e 7,24:1 em hover (medido por pixel no e2e, que também confere que o fundo é o verde). Os pares ficam em `landing-contrast.test.ts`, lidos do CSS real, e os helpers de cor dos dois testes de contraste passaram para `src/test/contrast.ts`. O spotlight dos cards e a vinheta de avatares de "Para quem" saíram (o cadeado de "Segurança" ficou, e é a única vinheta). As figuras de anomalias e de lançamento do tour, que estavam no tema escuro, foram recapturadas no Hologram pelos mesmos cenários do gate de a11y que as geraram (mesmo dado mockado, mesmo recorte). A troca automática das abas passou a ser explicada por uma linha de 2 px na aba ativa, que cresce nos 6 s do relógio e fica cheia quando ele para, e o "Pausar/Retomar" virou botão só de ícone com os mesmos nomes acessíveis. Medido por pixel no Hologram, sem mudança: título do passo ativo 16,4:1, dígito sobre a pastilha acesa 13,2:1, passo apagado 8,0:1, barra do tour 7,57:1._

_Versão 1.66 — 30/09/2026. **A landing ganhou o tour com prints reais e ritmo abaixo do hero (86e3gqfkf e 86e3gwzj0, feedback do Laio: "o início é mais encantador; dá para enxugar o texto e colocar mais dinamicidade").** O texto abaixo do hero caiu pela metade, sem afirmação nova, com tetos por campo em `content.test.ts` (card de público 110, dor 120, resposta 160, passo 110, item de segurança 120, frase do tour 120, bullet 80) e o `COPY.md` acompanhando linha a linha. A seção "Veja o Hologram OS por dentro" mostra cinco telas reais (conciliação, anomalias, lançamento no Omie, de-para, carteira) em abas, numa moldura de navegador: os PNGs saem de `Docs/manual/fonte/img/` reduzidos a 1600px e quantizados (39 a 90 KB), com largura e altura conferidas no cabeçalho do PNG por teste, e `next/image` `unoptimized`, porque o web roda `standalone` sem `sharp` e o otimizador do Next 14 responderia 500 (o slot `LandingImage` da landing tem o mesmo risco no dia em que for ligado). As abas trocam a cada 6 s só com a seção na tela, pausam sob o ponteiro, param no primeiro clique ou foco e têm botão de pausar e retomar (WCAG 2.2.2, desenho do carrossel do APG); com `forceMount` o Radix passa `hidden={false}` a todo painel, então o inativo recebe `hidden` explícito. O "Como funciona" acende um passo por vez em loop de 12 s pelo `reveal.tsx`, com a linha de progresso medida no DOM até a pastilha ativa; os pares de dor e resposta ganharam ícone, seta que se desenha e resposta 200 ms depois; "Para quem" e "Segurança" ganharam vinhetas SVG (no máximo 20 elementos, entrada de 600 a 900 ms e depois parada). Medido por pixel no Hologram: título do passo ativo 16,3:1, dígito sobre a pastilha acesa 13,1:1, passo apagado 8,0:1, título da barra do tour 7,57:1. **Regra que fica para o gate local:** com `E2E_SHOTS=1` e vários workers, a medição por pixel do hero em 390px passou a falhar com "Unable to capture screenshot" (o print inteiro em 390px tem 21 mil pixels de altura com DPR 2,75); retentativa e `test.slow` não resolvem, e isolada ela passa. Tire os prints com `--workers=1`; o gate em si (sem prints, como no CI) roda com os workers de sempre._

_Versão 1.65 — 30/09/2026. **O produto se chama Hologram OS (decisão do Pedro), e a landing ganhou presença (86e3gr6k5, que fecha o executável de 86e3fr9x3 e 86e3fr9wm).** `PRODUCT_NAME`, `PRODUCT_TITLE` e `PRODUCT_SHORT_NAME` valem "Hologram OS" (sem sigla nova), o OpenAPI é "Hologram OS API", nasceu `PRODUCT_DOMAIN = 'hologramos.com.br'` (domínio ainda não registrado) e a empresa segue "Hologram Gestão". A landing nomeia o produto (a D2 do plano caiu); o texto do consentimento não mudou, então a versão dele também não. Os testes de marca passaram a escrever os literais ANTIGOS: derivados da constante, eles procurariam o nome novo e deixariam o antigo voltar calado. Dois textos fora da tela trocaram e merecem aviso: o prefixo dos alertas virou `[Hologram OS]` (`ALERT_PREFIX`; filtro de e-mail ou canal montado sobre `[ADL]` precisa ser refeito) e a nota de resolução do lançamento no Omie lê a constante. O que é identificador FICOU: o prefixo `ADL` do `cCodIntLanc` (chave de dedup do que já foi lançado, §3.16) e o `ADL-PARSE-*`. A URL absoluta da landing (`metadataBase`, canônica, `sitemap.xml`) só existe com `NEXT_PUBLIC_SITE_URL`, build-arg a ligar no dia em que o domínio apontar. O refino visual é só CSS escopado e o `reveal.tsx`: palavra do título em gradiente (stop claro misturado a `--foreground`, medido por pixel a 7,67:1 no desktop e 6,62:1 em 390px), pílula com ponto pulsante, chips de formatos, mini-card flutuante na vinheta, rótulos de seção, divisores e filetes, grade atrás de "Como funciona", ruído SVG no fundo, borda em gradiente e spotlight nos cards. O spotlight pinta ATRÁS do texto e o axe não o enxerga, então o e2e mede o pior caso por pixel (texto `muted` no centro do brilho: 5,54:1); ele só existe com mouse e sem movimento reduzido, e o cenário do e2e AFIRMA a ausência no projeto de toque, porque o gate reprova teste pulado. A medição por pixel passou a esconder com `visibility: hidden`: `color: transparent` não apaga texto pintado por `background-clip: text`. A landing também oferece o manual: `public/manual-hologram-os.pdf` (cópia do `Docs/manual/Manual-Hologram-OS.pdf` da `main`), liberado no `robots.txt`, num botão secundário no fim da seção de segurança e na confirmação do formulário, com `download`, na mesma aba e com o tamanho ao lado; um teste falha se o PDF mudar e o "3 MB" não. **Regra que fica para o gate local:** efeito visual novo encarece o print de página inteira (3 a 6 s por captura no Pixel 5), e o cenário que tira três prints passou de 30 s só com `E2E_SHOTS=1`; o CI não tira prints. Antes de subir timeout, medir cada passo (`DEBUG=pw:api`) e cada efeito desligado um por vez: o mais caro na captura é o `blur` da aurora, que já existia._

_Versão 1.64 — 29/09/2026. **A raiz do sistema virou uma landing pública com captação de leads, e o nome do produto passou a morar num lugar só (épico 86e3fr9tj).** A `/` deixou de redirecionar para o login: é o grupo `app/(public)/` (landing e `/privacidade`), público no `middleware.ts` (`PUBLIC_PATHS`; com sessão, `/` vai para `/clientes`), com tema Hologram fixo pelo wrapper `.hologram`, largura contida e efeitos só em CSS escopado mais um componente cliente, tudo parado sob `prefers-reduced-motion` (§7 Frontend). O formulário grava em `leads` (migration `490bffa3f6e2`, reversível) pelo `POST /api/v1/leads` sem autenticação, que entrou em `NON_TENANT_ENDPOINTS` (15 → **16**) sem mexer na lista canônica (**116**); a §4.5 ganhou a exceção do lead em claro (prospect sem tenant, decisão do Pedro de 28/09), com o mínimo de campos e sem IP. Anti-spam: honeypot e 3 por e-mail em 24 h respondem o mesmo 200 sem gravar; o `slowapi` de 10/min é teto GLOBAL por instância, porque atrás do BFF a API vê o IP do proxy (86e3anx10). O aviso no Slack é inline, fail-soft e com timeout de 3 s, com mrkdwn escapado e log só da categoria da falha; `LEADS_SLACK_WEBHOOK_URL` não conta como canal de plantão, pelo mesmo motivo do sintético (§3.14), e a secret `leads-slack-webhook-url-<env>` precisa existir antes do deploy. O redactor passou a mascarar chaves `webhook` e `url`. O nome do produto saiu de cinco literais para `lib/brand.ts` e `core/branding.py`, com testes que recusam o nome escrito à mão em qualquer outro arquivo: a troca pelo nome novo (86e3fr9wm, bloqueada) é uma linha de cada lado. A doc gerada de endpoints sensíveis entrou no `.prettierignore`, como o contrato: o hook reescrevia o arquivo inteiro. Matriz (**29**) e pares de AAD (**17**) não mudaram._

_Versão 1.63 — 29/09/2026. **Os quatro candidatos que sobraram do épico de follow-ups da S14 viraram task e foram pagos, mais o achado do de-para que estava solto.** Três são a MESMA falha em lugares diferentes: a tela oferece algo que o destino não entrega. **86e3g9uku** — o painel do cliente sem conciliações mostrava "Criar conciliação" apontando para a LISTA, onde a criação já estava escondida para cliente sem origem (S9) e para cliente só-arquivo (S14, `CAPACIDADE_AUSENTE`); agora o estado vazio pergunta `originCodeFor(…, 'listar_contas')`, a MESMA chamada da lista, e o rótulo virou "Ir para conciliações" (o link navega, não cria). **86e3g9u3w** — a gaveta de conexão listava todos os tipos, e escolher "Arquivo" num cliente com Omie levava 409 `ORIGEM_JA_CONECTADA` depois do formulário inteiro preenchido; nasceu `connectableProviderTypes` (`lib/origin-capabilities.ts`), que projeta a trava do §4.8: com uma conexão que lista lançamentos, some todo tipo que também lista e é DIFERENTE dela — o mesmo tipo fica, porque duas conexões Omie com rótulos distintos são permitidas. `PROVIDER_TYPES` ganhou `listsLedger` pelo mesmo motivo que já carrega `requiresCredentials`: na criação não há conexão para perguntar à capacidade. **86e3g9ua7** — no de-para do cliente por arquivo o estado da base dizia "Nunca sincronizada"/"Sincronizada em", mandando procurar um botão "Sincronizar" que a própria tela já trocou por "Enviar arquivo do mês"; o texto passou a ramificar por `originIsFileBased`, a mesma resposta que ramifica a ação. **86e3g9v0j** — `title_contexts.text_iv` era `String(32)` no banco e `String(24)` no modelo: a migration da S15 escreveu um literal em vez de `IV_HEX_LENGTH`, e o IV é `os.urandom(12)` em hex, sempre 24. Nada quebrava, mas o `alembic check` acusava drift em toda entrega; migration nova alinha a coluna (a da S15 não se reescreve) e um teste varre o metadata atrás de coluna `_iv` com literal. **86e3g3dg3** (do épico da S16) — planilha de de-para exportada de OUTRO destino era recusada como `CONTAS_DA_PLANILHA_INVALIDAS` porque a pré-validação de contas não filtrava por `line.destination`; agora usa o mesmo filtro do laço que rejeita com `destino_diferente`, então a recusa aponta o arquivo errado em vez do plano de contas. Três regras novas: duas na §7 Frontend (ação tem de existir no destino, pela MESMA chamada dos dois lados; a palavra do estado acompanha a origem) e uma na §7 Backend (tamanho de coluna derivado de constante usa a constante). Endpoints sensíveis (**116**), matriz (**29**) e pares de AAD (**17**) não mudaram._

_Versão 1.62 — 29/09/2026. **Dois follow-ups em aberto de sprints anteriores foram pagos: a recusa de cabeçalho não ecoa mais a planilha, e a aba "Origem por arquivo" parou de ser invisível.** **86e3fvffy (achado do QA da S16, não bloqueante)** — as duas checagens de cabeçalho devolviam a linha 1 do arquivo CRUA em `details` (`foundColumns`, mais `unexpectedColumns`/`repeatedColumns` no plano contábil). Numa planilha enviada SEM cabeçalho a linha 1 é DADO, então o 422 passava a conter nome de conta (S16) ou descrição de lançamento (S14) — o que a §4.1 manda cifrar e a §4.5 manda nunca devolver. Agora `details` só nomeia o vocabulário NOSSO (colunas do modelo, colunas do mapeamento, e a repetição quando a coluna repetida é do modelo) e o que veio do arquivo vira contagem (`foundColumnCount`, `unexpectedColumnCount`); a tela troca a lista de "colunas encontradas" pelo diagnóstico que ela de fato entregava ("a planilha tem N colunas, M fora do modelo. Confira se a linha 1 é o cabeçalho"). A opção de filtrar por heurística ("parece cabeçalho") foi recusada pelo Pedro: nome de conta passa no teste. `SEM_MAPEAMENTO` mantém `foundColumns` como exceção DECLARADA — mostrar as colunas é a função daquela resposta. Regra nova na §7 Backend. **86e3fqnc9 (follow-up da validação da S14)** — o item "Origem por arquivo" só aparecia para o cliente que JÁ tinha conexão `arquivo`, e o resultado era um recurso invisível: quem opera não descobria que dá para atender cliente sem ERP mandando a planilha do mês. Esconder nunca foi regra da §4.9 (ler a aba é `AccessibleClientDep`, ninguém seria negado); o que o servidor nega é CONECTAR. A aba passou a ser sempre listada e a TELA explica três estados — encerrado, origem de outro tipo já conectada (conectar seria 409 `ORIGEM_JA_CONECTADA`, então não há botão) e sem origem nenhuma —, com "peça ao administrador" para quem não tem `manage_client_connections`. Qual origem está no caminho é decidido pela CAPACIDADE (`listar_lancamentos`), nunca por `provider_type === 'omie'`. "Conectar origem por arquivo" leva ao painel com a gaveta já aberta no tipo Arquivo (`?conectar=<tipo>`, lido uma vez e apagado da URL). Regra nova na §7 Frontend. Endpoints sensíveis (**116**), matriz (**29**) e pares de AAD (**17**) não mudaram: nenhuma rota nem permissão nova._

_Versão 1.61 — 29/09/2026. **O topo das telas do cliente encolheu (épico 86e3fr9pd, feedback do Lucas em 28/09).** O `ClientShell` perdeu o cabeçalho inteiro: breadcrumb, nome do cliente, selos de status e categoria, favorito e o menu "Ações do cliente". A task pedia o nome da página no lugar do nome do cliente, mas toda tela já tinha o próprio título logo abaixo, e o Pedro decidiu (29/09) que o título da TELA vira o `<h1>` e o shell não repete nada; o detalhe da conciliação já tinha o dele ("Conta · Mês"). Editar e Encerrar foram para a linha da lista de clientes (o ícone de encerrar entrou ANTES de o menu sair, para o encerramento nunca ficar sem botão), e o cliente encerrado ganhou o aviso de somente leitura que o selo dava. `sessionIdFromPathname` e `sessionCrumbLabel` ficaram sem uso e saíram. Carteira, de-para e plano de contas ganharam a moldura única de totais recolhíveis (`collapsible-summary.tsx`), e a carteira e o de-para passaram a ter abas, filtros e ações numa linha só. O "Novo Cliente" esconde App Key e App Secret atrás do switch "Conectar com o Omie", desligado por padrão, e desligar limpa a credencial. Duas regras novas na §7 Frontend. Endpoints sensíveis, matriz e pares de AAD não mudaram: só front._

_Versão 1.60 — 29/09/2026. **As 3 subtasks abertas do épico de follow-ups da Sprint 16 (86e3fxqq7) foram entregues num PR só, fora do sandbox dos agents.** A 4ª (desbloquear a S13) já estava `done` desde a v1.59. **86e3fxqqa — a resposta antes do commit, achado desde a v1.57, está CORRIGIDA**: `CommitBeforeResponseMiddleware` (`app/core/response_ordering.py`, novo §7 Backend) comita dentro do `send`, no `http.response.start`, então nenhum byte sai antes do `commit()` — em toda rota, sem tocar em nenhum `routes.py`. Medido em servidor uvicorn real: 45/100 leituras imediatas sem o dado antes, 0/100 depois (`httpx.ASGITransport` não reproduz a corrida: roda a app inteira numa coroutine só). **A primeira versão desta correção bufferizava a resposta inteira e foi REPROVADA na revisão**: o Starlette roda `await self.background()` dentro de `Response.__call__`, então o buffer segurava a resposta até a BackgroundTask acabar — 3,00 s contra 0,00 s numa task de 3 s, e até 900 s nos 4 endpoints de conciliação. Nenhum teste pegava: todos stubam `_schedule_reconciliation_processing`. Agora existe um cenário com BackgroundTask REAL, que reprova contra a versão bufferizada. **86e3fxqqe — a portabilidade do de-para (Sprint 12) volta a importar no destino `conta_contabil`**: a planilha ganhou a coluna `historico`, resolvida pela MESMA cifra/decifra da decisão manual (`accounting.py`); conta inexistente, sintética ou inativa recusa a planilha INTEIRA (`CONTAS_DA_PLANILHA_INVALIDAS`, molde da `FileLinesInvalidError` da S14) — histórico acima do limite recusa só a linha. `MappingImportUnavailableError`/`IMPORTACAO_INDISPONIVEL_NO_DESTINO` saíram do código. **86e3fxqqh — a prévia "Partida contábil" mostra o nome da categoria**, pela mesma resolução da lista de decisões: `resolve_category_names` saiu de método privado de `ClientMappingListService` para função do módulo `listing.py`, chamada agora também por `ClientMappingApplyService` — as duas telas não podem mais divergir porque são a MESMA chamada. Vitest **1021** (o total, já com os novos), contrato com diff só do esperado (`categoryName`/`categoryNameResolved` e o texto das rotas de import). Pares de AAD, endpoints sensíveis e matriz de permissões **não mudaram** — nenhuma rota nem permissão nova, só comportamento de rotas e campos existentes._

_Versão 1.59 — 29/09/2026. **A Sprint 13 (exportador para sistema contábil) entrou no primer, e o primer foi restaurado pela QUARTA vez.** O commit do QA (`1012e8e`) levou o `CLAUDE.md` do worktree (o prompt do papel, 87 linhas) por cima das 1311 do primer, e a edição do `PROJECT.md` foi negada na sessão dele; o patch ficou no `HANDOFF.md` e a validação humana (86e3fypch) o aplicou com cada número conferido por comando: §3.15 com as 8 rotas novas (108 → **116**) e `GET /export-layout-templates` fora com motivo; §4.9 com `generate_accounting_file` e `manage_export_layouts` (27 → **29**) e o porquê de `review_export` não servir; §4.12 com as gerações (ficam no encerramento, saem antes das materializações na exclusão definitiva); §8 com a linha e o parágrafo da S13. Pares de AAD seguem **17**. As 4 decisões do planejador da sprint (quem lê layouts, quem lista gerações, o `<N>` do nome do arquivo e as casas decimais) foram validadas pelo Pedro e mantidas. A causa desta vez foi dupla: o `AGENT_PATHS_QA` alcança o `CLAUDE.md` do worktree, e o hub semeou os `PROJECT.md` de uma `develop` LOCAL sem a S16. O hub passou a semear do commit de onde o worktree saiu, a tirar o `CLAUDE.md` do worktree de todo commit e a recusar primer que encolhe ou perde a última `_Versão` (86e3fyjan, repositório `agents-hub`); a skill `sprint-preflight` exige a `develop` local igual à do origin. A validação rodou o que o QA não pôde: pytest completo contra Postgres em Python 3.12 (3920 passed, 0 failed), ciclo das 2 migrations, contrato com diff 0, vitest 1020, a11y 558 por tema nos três temas, 102 verificações pela API (o arquivo gerado da amostra é o CSV real do escritório byte a byte) e prints desktop e 390px. No mesmo PR entraram os dois outros follow-ups do QA: prefixo do valor e cabeçalho de coluna que começam com `= + - @` são 422 (`FORMULA_PREFIXES` virou fonte única, na definição do layout), e três estados do front (diálogo de layout que não resetava, recusa velha na tela depois de materializar de novo, diálogo do modelo fechando com o POST em andamento). A resposta antes do commit (86e3fxqqa) apareceu de novo, duas vezes, no cenário pela API._

_Versão 1.58 — 29/09/2026. **As 6 decisões do planejador da Sprint 16 foram validadas pelo Pedro e todas mantidas**, e a §4.9 deixou de chamar a exclusão do `client_manager` de pendente. As outras cinco não estavam no primer: modelo de planilha próprio para o plano contábil (o export nativo do Domínio fica para um futuro talvez, por não ter colunas documentadas), decisão legada do catálogo em `conta_contabil` incompleta sem bloquear, importação de de-para por planilha recusada nesse destino até a planilha levar o histórico (86e3fxqqe), conta padrão do banco só para linha sem conta de origem, e bloqueio por falta de banco só em linha com conta decidida. O registro fica na página da Sprint 16; os follow-ups da validação, no épico 86e3fxqq7._

_Versão 1.57 — 28/09/2026. **A Sprint 16 (plano contábil do cliente e partida completa) entrou no primer, e o primer foi restaurado pela TERCEIRA vez.** O commit do QA levou o `CLAUDE.md` do worktree (o prompt do papel QA, 10.755 bytes) por cima do primer (162.607 bytes na develop), como nas S14 e S15; e a edição do `PROJECT.md` foi negada na sessão dele, com o texto deixado no `HANDOFF.md`. A validação humana (86e3fw419) restaurou da `develop` e aplicou à mão, com cada número conferido por comando no HEAD da branch: nota do topo e §3.15 com a lista canônica em **108** (as 4 rotas do plano contábil e da conta do banco), §4.1 com os pares de AAD em **17** (`client_accounting_accounts.name_encrypted`, `client_mapping_decisions.history_encrypted`), §4.9 com a matriz em **27** (`manage_client_accounting_chart`, do staff, e por que o `client_manager` fica de fora ao contrário do de-para), §4.12 com o purge da associação e do plano e a leitura do histórico de materialização retida (vazio ou `[indecifrável]`, nunca 500), dois reforços de regra que o QA pediu (§7 Backend: o limite do schema é o da COLUNA de destino, `max_length` com teste que amarra os dois; §7 Frontend: aviso de outra tela que aponta para a ação passa pelo mesmo gate), e a linha e o parágrafo da S16 na §8. O resto da validação rodou fora do sandbox: pytest completo contra Postgres em Python 3.12, como o CI (3622 passed, 0 failed), ciclo das 3 migrations, contrato com diff 0, vitest 962, a11y 514 por tema nos três temas, 50 verificações pela API e 9 prints pela tela. As 5 falhas da primeira rodada da suíte eram de TESTE nunca executado (fixture sem a conta do banco que a 16.3 passou a exigir; releitura de coluna que não repovoa a entidade da sessão compartilhada), com o produto provado certo pela API real. **Achado que fica, anterior à sprint e de toda a API:** a resposta sai antes do `commit()` da `get_db_session`, medido (14 de 15 criações com o `201` antes do dado, leitura velha na tela depois de salvar, e uma escrita que passou pela trava de cliente encerrado 76 ms depois do `204`); a correção muda a política de transação da API inteira e é decisão do Pedro._

_Versão 1.56 — 28/09/2026. **A Sprint 14 (origem por arquivo) entrou no primer, e o primer foi restaurado no PR dela.** O commit do QA (`fe7c4bc`) levou o `CLAUDE.md` do worktree, que é o prompt do papel QA, por cima do primer (+80/−1110 no PR #231), a mesma falha da Sprint 15; e as edições que o QA disse ter feito no `PROJECT.md` nunca existiram (a sessão dele teve a edição negada, o texto ficou no `HANDOFF.md`). A validação humana (86e3fcp76) restaurou o primer da `develop` e aplicou à mão: §3.15 com a lista canônica em **104** (as 5 rotas do mapeamento de entrada e do envio), §4.1 com os pares de AAD em **15** (`client_file_categories.label_encrypted`, `client_movements.description_encrypted`), §4.8 com a origem `arquivo` (adaptador vazio, sem credencial, só `listar_lancamentos`, DEK provisionada na criação da conexão) e a regra "um cliente, um tipo de origem de lançamentos" (409 `ORIGEM_JA_CONECTADA`; só-arquivo nos consumidores do Omie é 409 `CAPACIDADE_AUSENTE` antes de gravar), §4.9 com a matriz em **26** (`upload_client_file` para os 5 papéis, `manage_input_mapping` sem o `client_operator`, e por que são próprias), §4.12 com o purge do mapeamento, das categorias do arquivo e das importações, três regras na §7 Backend (UPDATE em lote com WHERE extra é Core; dinheiro de arquivo de terceiro no formato estrito do separador; log se afirma com `capture_logs`), e a linha e o parágrafo da S14 na §8. Todo número foi conferido por comando no HEAD da branch. O resto da validação rodou fora do sandbox e passou: pytest completo com `--cov` contra Postgres (3402 passed, os 3 ambientais de alerting, 87%), a11y nos três temas (448 cada, depois de repetir o Hologram sozinho: na primeira rodada, com a máquina em load 30, 6 cenários ANTIGOS estouraram `newPage`), contrato com diff 0, ciclo das 3 migrations num banco limpo, e o cenário de ponta a ponta pela API (73 verificações) e pela tela. **Regra que fica:** rodada de a11y e suíte do backend ao mesmo tempo, na mesma máquina, produz falha de timeout que parece regressão; confira a carga antes de ler o vermelho, e repita sozinho antes de concluir qualquer coisa._

_Versão 1.55 — 28/09/2026. **A linha de saldo do extrato tem DUAS formas, e a conta corrente usa a natureza `P`/`R` do cartão.** Cinco conciliações do Laticínio (BB CC, janeiro/2026) caíram em `INTERNAL_ERROR` com o mesmo `ValidationError`: na conta corrente a Omie devolve a linha "SALDO ANTERIOR"/"SALDO" COM `nCodLancamento` — um contador 1, 2, 3…, não um ID —, e o filtro de `listar_extrato` só descartava linha SEM `nCodLancamento` (forma do cartão e do caso Austral). "Tentar novamente" não resolvia: a Omie devolve sempre a mesma linha. O critério virou `is_extrato_summary_row` (sem `cNatureza` e valor zero), único, usado por produção e pelo gate de fixtures; linha sem natureza e COM valor segue falhando alto, porque descartá-la calada sumiria com um lançamento real. A captura (só leitura, 28/09) entrou anonimizada como `listar_extrato_conta_corrente.response.json` e mostrou a segunda descoberta: a conta CORRENTE também vem `P`/`R` com valor já sinalizado (113/113), e não `D`/`C` absoluto como a doc diz — o `signed_amount` já estava certo, a §5 e a skill `omie` estavam erradas. A correção vale para os quatro consumidores do extrato (processamento, cache da revisão, sync de movimentos da S12, reconciliação do posting)._

_Versão 1.54 — 26/09/2026. **A plataforma redefine a senha de qualquer usuário e isso derruba as sessões dele (86e3ewukz), e a tela de login diz o que fazer a quem esqueceu a senha (86e2u5140).** Até aqui não havia caminho nenhum para trocar senha depois do cadastro, nem revogação de sessão: numa conta comprometida, o invasor seguiria dentro por até 7 dias. Nasceram `POST /users/{id}/password` (lista canônica 98 → **99**, com os três atacantes), a permissão `reset_user_password` (matriz 23 → **24**, só plataforma; o admin da própria organização do alvo é ❌ porque é suporte, não gestão), `users.password_changed_at` (migration reversível) e a regra do §3.12: `get_current_user` e o refresh recusam token com `iat` anterior ao carimbo — comparação em segundos inteiros, porque `iat` é inteiro e `<=` recusaria o login feito no mesmo segundo da redefinição. O mínimo da senha é o do TIPO do alvo (8 staff, 10 usuário de cliente), das constantes da criação (`STAFF_MIN_PASSWORD_LENGTH`, `CLIENT_USER_MIN_PASSWORD_LENGTH`); a própria senha é 409 tipado; tenant encerrado é 409; a senha nunca entra em log, resposta nem no evento `senha_redefinida_pela_plataforma` (só IDs e o escopo do alvo). Na tela, a ação "Redefinir senha" (só `reset_user_password`) vive na lista de staff, na aba de administradores da plataforma (que deixou de ser "sem ação nenhuma": esta é a única) e na lista de usuários do cliente, num `AlertDialog` com confirmação e o aviso de que os acessos abertos serão encerrados. Decisões pendentes da task, fechadas como recomendado: outro administrador da plataforma pode ser alvo; a plataforma digita a senha; forçar a troca no próximo login fica para quando a 86e2n39hg existir. O login ganhou a linha "Esqueceu a senha? Fale com o administrador da sua conta", genérica de propósito (§3.9)._

_Versão 1.53 — 26/09/2026. **A carteira deixou de encher a janela: a página rola, a tabela não, e o cabeçalho gruda (86e3eq9uy, fecho do épico 86e3eq9un).** O padrão `<Table fill>` deixava 2 ou 3 linhas visíveis num notebook (cabeçalho do cliente, abas, dois cards de totais e quatro filtros ficavam fixos acima). Nasceu o segundo padrão de altura no primitivo (`<Table stickyHeader="page">` + `<TableCard pageScroll>`), opt-in e só na carteira: altura natural, quem rola é o `<main>`, cabeçalho `sticky` de `xl` para cima, wrapper e card com `overflow-clip` (porque `sticky` gruda no scroller mais próximo e `overflow-hidden` também é scroller), abaixo de `xl` o comportamento de sempre (em 1024px as sete colunas não cabem, e cortar coluna em silêncio é pior que não grudar). Os sete valores de cada card de totais viraram botões que filtram pela URL (tabela `summaryFilterParams`, fonte única; "Em aberto" leva `situation=em_aberto` para o total da paginação bater com o card; ativo = `aria-pressed` + `accent`, com TODO o texto em `accent-foreground` para não criar par sem teste), com desfazer no segundo clique; Situação e Balde saíram da barra (os parâmetros ficam, link antigo funciona e vira etiqueta removível); Tipo virou grupo de botões; linha de sete colunas mais baixa, com "Contexto" visível e clique na linha abrindo a gaveta (sem `tabIndex`/`role` no `<tr>`); "Ações do cliente" virou menu `modal={false}` no shell, só com Editar e Encerrar, com o diálogo aberto em `onCloseAutoFocus` (na tarefa seguinte) para nunca haver dois overlays, e `DialogContent`/`AlertDialogContent` passaram a devolver o foco a quem o tinha ao abrir, capturado por `OpenerCapture`, filho do conteúdo (o Radix devolve só ao `DialogTrigger`, que um diálogo aberto por estado não tem, e `onOpenAutoFocus` nem dispara quando um campo com `autoFocus` já puxou o foco); o `th` grudado desconta o padding do `<main>` por `--page-scroll-padding`; "Atualizado em" ao lado de sincronizar; `DEFAULT_PAGE_SIZE` 50; "aging" saiu do texto visível. Regra na §7 Frontend, na skill `front-gate` §1 e no `.claude/design-system.md`. De quebra, o rodapé da v1.48 e a skill §4 deixaram de tratar o hover da célula de qualificação como achado: botão só de ícone, limite 3:1, passa._

_Versão 1.52 — 26/09/2026. **Os seis achados do QA da Sprint 12 sobre o de-para e a base de movimentos foram pagos (follow-up 86e3f0ux7).** A base ficou honesta em dois pontos que a §8 não cobria: o cache de contas é renovado pelo `get_or_sync` de sempre (TTL de 24h, mesmo lock, em sequência e nunca aninhado) ANTES de ler a origem, e a marcação de `ausente_na_origem` é recortada pelas contas LIDAS na passada (conta fora do cache não foi consultada, e os movimentos dela continuam existindo). Falha de BANCO no meio do ciclo passou a carimbar `sync_failed_at` (rollback, carimbo, commit como barreira, re-levanta), como a falha de origem já fazia. As escritas do de-para (decisão, herança, materialização) passaram a rodar sob `pg_advisory_xact_lock` por (cliente, destino): a checagem "competência já materializada?" e o INSERT não se cruzam mais entre duas requisições. Nasceu `GET …/mapping/{tipo}/materializations` (lista canônica 97 → **98**; autor por `author_for_viewer`, só o cabeçalho da versão), o lote `confirm-inherited` aceita o recorte por código da lista, e `depara_aplicado` ganhou `competencia` (lacuna do PRD: a leitura D+30 por competência era impossível). Contrato e doc de endpoints regenerados. **Regra que fica:** o mesmo lock que serializa a origem por credencial não serve para serializar ESCRITAS concorrentes no banco; para isso é lock transacional do Postgres por chave de negócio._

_Versão 1.51 — 26/09/2026. **A validação humana da Sprint 12 (86e3f14qc) rodou fora do sandbox o que a sprint não conseguiu, e o produto passou.** Suíte completa com Postgres (2903 verdes, 3 ambientais), a11y em browser nos três temas (384 por tema), contrato com diff 0, ciclo de migrations num banco limpo, cenário de API de ponta a ponta (a tese dos dois destinos nos números, os 409 tipados, export/import, 403 do operador e cross-tenant sem vazar nome, trilha e eventos) e prints. **Um defeito, invisível ao axe:** o `TabsContent` inativo fica montado com `hidden`, a classe `flex` do consumidor vence o atributo e o `flex-1` faz o painel invisível crescer na sobra da coluna: 209px de vão entre as abas e a prévia do de-para quando a prévia é curta. Corrigido no primitivo (`data-[state=inactive]:hidden`), com teste de classe e guarda geométrica no e2e; a regra entrou na skill `front-gate` §1. **Regra que fica:** painel condicional que recebe classe de display esconde por variante de estado, nunca pelo atributo. Ficaram para o follow-up de front: destino padrão da tela ("Conta contábil", o primeiro do catálogo, em vez do demonstrativo) e "Confirmar herdadas" em destino que não herda. As seis decisões do planejador (ADR-074/075/077-BE) seguem pendentes do Pedro; S-1 segue sem medição._

_Versão 1.50 — 26/09/2026. **Re-revisão da Sprint 12: as 5 tasks reprovadas voltaram corrigidas e a sprint foi aprovada inteira (ADR-040-QA).** A §7 Backend ganhou três regras que saíram dos defeitos da rodada 1: falha esperada nunca é 500 (padrão que recusa o que o construtor não aceita, props de métrica pós-commit dentro do fail-soft, arquivo de terceiro com 400 único `from None` e custo limitado antes de iterar, UNIQUE alcançável por corrida tratada com `ON CONFLICT DO NOTHING` ou SAVEPOINT → 409); "o mês de agora" no fuso do Brasil por `client_movements/competence.py::current_competence`; e teste de integração que não rodou não é verde (releitura async com `populate_existing`, nunca `expire_all()`). A nota da Sprint 12 na §8 passou a registrar a aprovação. Nenhum número do primer mudou (97 endpoints, 23 permissões, 13 pares de AAD)._

_Versão 1.49 — 26/09/2026. **A Sprint 12 (de-para multi-destino) entrou no primer.** §3.15 com a lista canônica em 97 (as 20 rotas novas: movimentos, catálogo por organização e de-para), §4.9 com as três permissões novas e o porquê de cada uma ser própria (`manage_client_mapping` inclui o manager de propósito; `manage_mapping_catalog` é da organização), §4.12 com movimentos e decisões no purge e as materializações RETIDAS, e §8 com o que a sprint deixou no código. Nenhum AAD novo. Escrito pelo QA na rodada 1, com 5 tasks ainda em retrabalho (ADR-039-QA): se o retrabalho mudar algum destes fatos, a rodada seguinte corrige aqui._

_Versão 1.48 — 25/09/2026. **Fundo destrutivo de badge ou de hover é `destructive-muted`, nunca `bg-destructive/N` (86e3dxund).** O QA da Sprint 11 mediu o badge `/10` a 4,22:1 no escuro e 4,42:1 no Hologram, e o `theme-contrast.test.ts` dizia que o par passava: ele compunha os 10% sobre `background`, mas o badge vive em linha de tabela, e em hover (`hover:bg-muted/50`) a superfície de baixo é outra. Os três badges (status "Erro", usuário "Inativo", glossário "Indecifrável") e os quatro botões com `hover:bg-destructive/10` passaram ao token opaco, o caso enganoso saiu do teste unitário e o e2e ganhou três cenários com o ponteiro EM CIMA e contraste COMPOSTO (`contrasteComposto`, porque o axe dá `incomplete` para fundo translúcido e o `measuredContrast` trata o primeiro fundo como opaco); com as classes antigas eles reprovam 4,23:1 e 4,44:1, com as novas passam. Regra na skill `front-gate` §4 e no `.claude/design-system.md`. ⚠️ Na célula de qualificação da revisão, o `hover:bg-destructive/20` sobre `destructive-muted` dá 4,23 / 3,76 / 3,46:1 (claro / escuro / Hologram) e o `hover:bg-warning/20` 3,68:1 no claro (conta sobre os tokens): esta versão registrou isso como achado aberto, e a 86e3eq9uy corrigiu a leitura em 26/09/2026. **Não é defeito**: o botão é só ícone (`aria-label`, sem texto visível), e para ícone o WCAG pede 3:1 (critério 1.4.11), não 4,5:1. Todos passam; o mais justo é 3,46:1 no Hologram. Texto sobre fundo com alfa continua proibido; nenhuma task foi criada para o hover da qualificação (decisão do Pedro, 25/09/2026)._

_Versão 1.47 — 25/09/2026. **"Excluir cliente" saiu da tela, para todos os papéis (86e3eqxdt, decisão do Pedro).** A saída do cliente pela interface passa a ser só o encerramento com retenção; a exclusão definitiva continua inteira no servidor (`DELETE /clients/{id}`, `edit_client`, lista canônica e testes intocados) como caminho do apagamento LGPD. A §4.12 registra a exceção à §4.9, que normalmente manda a tela esconder só o que o servidor nega. No front saíram o diálogo, o hook, a chamada da API e os mocks mortos em 9 testes; o cabeçalho do cliente encerrado, que só tinha o Excluir, passou a não renderizar o grupo de ações. O e2e troca o teste do fluxo de exclusão por dois: o admin não encontra "Excluir cliente" em forma nenhuma, e o cliente encerrado não tem ação nenhuma no cabeçalho._

_Versão 1.46 — 25/09/2026. **Os dois débitos que a validação da Sprint 15 registrou foram pagos antes do `develop → main`.** A lista da carteira passou a dizer, na própria linha, quais títulos têm contexto: `contextCount` na resposta de `GET /titles`, preenchido por UMA query agrupada pela página (sem N+1, com `client_id` no próprio `WHERE`), só a contagem e nunca o texto; na tela, ícone em destaque com o número para quem tem contexto e apagado para quem não tem, e a contagem também no nome acessível ("2 registrados" / "nenhum registrado"). E o gate de a11y em browser ganhou três cenários (gaveta com histórico e formulário, gaveta só-leitura do operador, aba do relatório), agora 342 por tema. De quebra, um teste de unidade que ainda afirmava o piso antigo (`lg:min-h-0`) estava vermelho na `develop` e derrubava o job de web do #205: passou a travar os dois pisos (24rem e `lg:min-h-[8rem]`). **Regra que fica:** mudou classe de layout, roda o vitest da tela antes do commit — o teste de classe existe justamente para travar esse desenho._

_Versão 1.45 — 24/09/2026. **A Sprint 15 entrou (contexto do título) e a validação humana achou o primer inteiro APAGADO na branch da sprint.** O commit do QA do hub trocou o `CLAUDE.md` da raiz pelo PROMPT do papel QA (76 linhas no lugar de 977): dentro do worktree o `CLAUDE.md` é o prompt do agente e o primer é o `PROJECT.md`; como o QA desta sprint não editou o `PROJECT.md`, a materialização não rodou e o `gitPaths` do QA (que inclui `CLAUDE.md`) commitou o prompt por cima do primer. O PR #203 mergearia isso. A correção desta rodada restaura o primer da `develop` e aplica à mão o que a sprint muda nele; a correção do hub (nunca commitar `CLAUDE.md` do worktree quando o `PROJECT.md` não mudou) é do repositório `agents-hub`. O que a validação rodou: suíte completa com Postgres, lint/tsc/vitest (676), contrato com diff 0, gate de a11y nos três temas, cenário de API (registro com histórico append-only, 400 genérico em tipo e texto inválidos, filtro `hasNoContext` no servidor, relatório em dois grupos com o mais recente decidindo, 404 sem vazar para título de outro cliente, operador lê e não registra, autor mascarado como "Equipe Hologram" para o tenant) e prints desktop e 390px. O gate de a11y em browser também pegou uma REGRESSÃO de layout que o QA do hub não podia ver: a aba nova (`Tabs`) quebrou a cadeia de altura da carteira e a tabela parou de rolar dentro da própria área (o defeito 86e2uca1d de volta), porque `Tabs` e `TabsContent` são itens flex sem `min-h-0`; corrigido na mesma branch, e como a faixa de abas tirou ~56px da tabela, o estado "última tentativa falhou" passou a espremê-la a 75px em desktop, então a área ganhou um piso de 8rem de `lg` para cima. E a suíte com Postgres pegou 3 testes do relatório quebrados, todos de TESTE: o helper semeava um título por `reconcile_cycle` (o ciclo marca como `ausente_na_origem` quem não veio no payload) e o teste do "contexto mais recente" postava dois contextos na mesma transação da fixture, onde `now()` devolve o mesmo instante. **Dois débitos ficaram registrados, não corrigidos:** a lista de títulos não diz quais têm contexto (o ícone é igual em toda linha, o R2 pedia indicador) e nenhum cenário novo entrou no gate de a11y em browser para a gaveta e o relatório. **Regra que fica:** toda validação humana começa por `git diff --stat origin/develop...HEAD -- CLAUDE.md`; um primer que encolhe é a sprint apagando a lei._

_Versão 1.44 — 24/09/2026. **A validação humana da Sprint 11 (task 86e3dzp64) rodou o que o QA do hub não pôde rodar na aprovação, e o produto passou.** O sandbox dos agents tinha perdido o Docker entre as duas rodadas, então a aprovação da 2ª rodada foi sem integração e sem a11y em browser. Fora do sandbox: suíte completa com Postgres (2401 passed, 5 failed: 3 ambientais de alerting e 2 de TESTE), gate de a11y nos três temas (330 cenários cada, guard limpo), contrato regenerado com diff 0, cenário de API completo (sincronização, aging no servidor, 400 genérico em enum e `pageSize`, 409 `SEM_CONEXAO`, operador lê 200 e sincroniza 403, outro tenant 403, evento `carteira_sincronizada` com as 5 chaves e sem PII) e prints em desktop e 390px com os agregados empilhados sem sobreposição. Os 2 testes quebrados eram a correção da 1ª rodada do QA: `db_session.expire_all()` no helper `_titles()` expira também o `Client` do teste e o `client.id` seguinte faz refresh lazy fora do greenlet (`MissingGreenlet`, a mesma armadilha de 21/09); virou `populate_existing=True` no select do helper, que repovoa só o que a query devolve. **Regra que fica:** em teste async, nunca `expire_all()`; refresh de UM objeto ou `populate_existing` na query. E um defeito cosmético corrigido nas DUAS telas da família: a 390px o estado vazio dentro da tabela (`TableCell colSpan`) ficava cortado à direita porque a tabela rola na horizontal, e o plano de contas da S10 tinha o mesmo padrão. Nasceu `TableEmpty` (`ui/table.tsx`): o estado vazio de uma tabela de lista vai DEPOIS do `<Table>`, dentro do `<TableCard>`, nunca numa célula `colSpan`, e o e2e passou a medir o texto do estado vazio dentro da viewport em vez da altura da região._

_Versão 1.43 — 24/09/2026. **A carteira de títulos em aberto entrou no produto (Sprint 11 do hub), aprovada pelo QA em 2ª rodada e validada em 24/09 (v1.44).** `client_titles` persiste os títulos a pagar e a receber não liquidados **sem recorte de conta e sem recorte de competência** — é esse recorte invertido, e só ele, que faz um título vencido há quatro meses aparecer onde a conciliação (sempre uma conta, sempre um mês) nunca o traria; `processing/omie_fetch.py` não foi tocado. A §4.5 não abriu exceção: a tabela guarda código de devedor, nunca nome — o nome é resolvido em runtime e o caminho é **fail-soft** (origem fora do ar devolve o código com 200, não 500). A lista canônica foi de 71 para **74** e a §4.9 de 16 para **18 permissões**. **O que vale fora da sprint, e é o motivo de a 1ª rodada ter sido reprovada:** (1) símbolo usado como CHAMÁVEL nunca vai para `if TYPE_CHECKING:` — `mypy --strict` aprova **por definição**, `ruff` não distingue, e o `NameError` resultante deixou duas das três rotas da sprint respondendo **500** com 1.141 unitários verdes em cima; a regra virou gate de AST (`tests/unit/test_type_checking_imports_gate.py`, varre `app/` e `scripts/`); (2) código de erro de terceiro não se compara por igualdade sem a evidência do formato capturado — a Omie devolve `SOAP-ENV:Client-5001`, não `5001`, e a comparação exata tinha deixado um ramo de fallback inteiro como código MORTO; (3) predicado que decide DESVIO de caminho ganha teste unitário com o valor real, porque teste que só roda com Docker é teste que ninguém roda. ⚠️ **Pendência declarada desta rodada, maior que a das S9/S10:** o sandbox recusou o socket do Docker (`operation not permitted`), então **a suíte de integração, a bateria dos 74 endpoints sensíveis x 3 atacantes e o gate de a11y nos três temas NÃO rodaram** — nem o cenário com cliente real de dev, nem a medição da suposição S-1. O que sustentou a aprovação foi que os DOIS defeitos de produto da 1ª rodada passaram a ter teste **unitário** (1.153 verdes) e o contraste foi conferido por aritmética sobre os tokens (4,57:1 no Hologram, a margem mais apertada). A validação humana desta sprint é mais necessária que a das anteriores, não menos._

_Versão 1.42 — 23/09/2026. **A Sprint 10 está na `main` e em dev, e o primer deixou de dizer o contrário.** A v1.41 foi escrita pelo QA do hub com a sprint ainda na branch; a validação humana (task 86e3dqcf4) achou 6 testes de integração quebrados, todos de teste (tipo de exceção `OmieAuthError`, 400 em vez de 422, revisão fixa no guard de downgrade) e nenhum defeito de produto; a correção (`fix/S10-validation-findings`, PR #196) entrou no #195 e o `develop → main` fechou em 23/09. A §8 passou a dizer que as Sprints 9 e 10 estão na `main`, com a regra que as duas ensinaram: toda sprint do hub termina com validação humana FORA do sandbox, porque os agents não têm Docker e a integração, o a11y e o cenário pela tela só rodam de verdade ali. Nada mais mudou._

_Versão 1.41 — 23/09/2026. **O plano de contas do cliente virou entidade do produto (Sprint 10 do hub, na `main` desde 23/09).** Até aqui a plataforma **lia** categorias do Omie só para resolver código → descrição na tela de revisão; agora `client_chart_of_accounts` persiste, por cliente, código, hierarquia (`categoria_superior`, com a raiz `'0'` normalizada para NULL), situação, flags e o **vínculo com a conta de demonstrativo** — o insumo que o de-para da Sprint 12 herdaria pronto em vez de exigir digitação linha a linha. **A §4.5 não abriu exceção:** nenhuma coluna de nome, nem `descricaoDRE`, nem `tag_conta_contabil` (rótulo é nome) — o DTO os declara porque a origem os manda, e a persistência para no código; como nada ali é cifrado, a tabela entra em `close_client_purge` **explicitamente** (§4.12), senão sobreviveria ao encerramento. A §4.9 foi de 14 para **16 permissões x 5 papéis**: `view_client_chart_of_accounts` (todos) e `sync_client_chart_of_accounts` (todos menos o `client_operator`) são novas porque as duas reutilizações plausíveis erram em direções OPOSTAS — admin-only excluiria o gerente do escritório parceiro; "de todos" deixaria o operador forçar chamadas à origem. A lista canônica foi de 68 para **71**, `PENDING_ENDPOINTS` continua vazio. **O que vale fora da sprint:** (1) quando um catálogo já é lido em algum lugar, a leitura nova é um ACESSOR do caminho existente e o caminho antigo passa a chamá-lo — duplicar o hit/miss do cache faria duas telas verem catálogos diferentes, e o "fonte única" nasceria falso; (2) **código de categoria e código de conta de demonstrativo colidem** (`1.01.01` é "BPO Controller - RB" e "Receita Bruta de Vendas"), então resolver nome exige DOIS mapas — um só mostraria o nome errado e a tela pareceria certa; (3) `''` e bloco `{}` da origem colapsam em `None` **no DTO**, não em cada caller, senão "sem destino declarado" e "destino vazio" viram dois estados do mesmo fato e a contagem de cobertura passa a depender de qual deles a linha calhou de receber. ⚠️ **Os números do exemplo do PRD estão errados e o código está certo:** na fixture real são **46 ativas** (4 inativas), **33** com destino e **5** com conta contábil — os 37 e 6 do PRD são contagens sobre o TOTAL. A métrica (`com_destino ÷ ativas`) dá **71,7%**, não os 74% citados: acima do alvo de 70%, por margem bem menor. **Pendência declarada do QA desta rodada:** a suíte de integração, a bateria cross-org das 3 rotas novas, o gate de a11y em browser e o cenário ponta a ponta em dev **não rodaram** — sem Postgres e com o socket do Docker recusado pelo sandbox. O que rodou está no `HANDOFF.md`, com output._

_Versão 1.40 — 23/09/2026. **A validação humana da Sprint 9 (task 86e3dcm2y) foi a primeira rodada REAL da integração e do cenário pela tela, e achou o que o sandbox dos agents não podia achar.** Nove testes de integração da própria sprint reprovavam e os três jobs de a11y caíam em dois cenários e2e; o `develop → main` (#193) nasceu vermelho. Um defeito de produto: o "Testar conexão" da gaveta de origem usava a rota legada `test-connection`, que construía `OmieClient(...)` direto e saía para a rede com a credencial `FAKE_DEMO_OMIE_`, travando o cadastro em dev, no ambiente de demonstração e no e2e — agora passa por `build*omie_raw_client`, e a §4.8 ganhou a regra "todo client nasce no adaptador". Um descompasso de convenção: o PRD pedia "422 apontando a rota de conexões" e o handler global responde 400 genérico sem mensagem de propósito; nasceu `CredentialsMovedToConnectionsError`(422,`CREDENTIALS_MOVED`) na rota, o validador Pydantic saiu do schema, e a §4.8 fixa a regra geral (forma é 400 genérico; orientação é exceção tipada). Um de durabilidade: "recusa sem gravar" na criação com credencial inválida agora é SAVEPOINT em volta de cliente + carteira + conexão. O resto era teste: dois casos afirmavam 422 onde a casa responde 400, um lia envelope `data`que o PATCH não devolve, um teste antigo criava cliente com credencial`"k"/"s"`e passou a bater no Omie REAL depois que o cadastro verifica no provedor (prefixo mock), e no e2e`getByText('Com erro')`casava com dois elementos e o mock da lista ignorava o`originState`. Ensaio da conversão de credenciais no banco de dev com linha bare: converte, `--verify` PASS, idempotente.*

_Versão 1.39 — 23/09/2026. **O cliente deixou de ser "um par de credenciais Omie com nome" (Sprint 9 do hub, ainda na branch da sprint).** Ele é entidade plena **sem origem**: as 4 colunas de credencial de `clients` viraram nuláveis e a credencial mora em `client_connections` (0..N por cliente, `UNIQUE(client_id, provider_type, label)`), com a **DEK nascendo só na primeira conexão** — cliente sem origem não provisiona chave. §4.1 ganhou o **12º** par de AAD (`client_connections.credentials_encrypted`: **um** par para o JSON inteiro, para provedor de outro shape caber sem AAD novo); §4.8 ganhou o modelo de origem, a **taxonomia dos três 409** (`SEM_CONEXAO` conectar · `ORIGEM_COM_ERRO` reconectar · `CAPACIDADE_AUSENTE` não há o que consertar — são 409 porque são estado esperado da configuração, não falha) e a **precedência do fallback datado** (conexão vence coluna antiga; "desligar a flag não é promoção"); §4.9 virou **14 x 5** com `manage_client_connections`, e o **manager entra de propósito** — ele cria cliente, e sem a célula o gerente do escritório parceiro cadastraria a carteira sem conseguir conectar ninguém; §4.12 registra que encerrar e excluir levam as conexões na mesma transação. A lista canônica foi de 63 para **68** (as 5 rotas de conexão, na bateria dos três atacantes), `PENDING_ENDPOINTS` continua vazio. **Duas lições da revisão que valem fora da sprint:** escrita seguida de `raise` NÃO é provada por teste de integração (a fixture `client_with_db` não tem o `except: rollback()` da produção — durabilidade exige `commit()` explícito antes do `raise`, ou sessão própria); e fallback datado tem de cobrir o estado **derivado** que a UI usa para liberar ação, não só o caminho de execução — quem só DESCREVE não quebra com exceção, quebra com tela vazia, que passa por comportamento esperado._

_Versão 1.38 — 22/09/2026. **A lista de administradores da plataforma saiu do rodapé de Organizações e virou aba própria em Usuários (task 86e3chrxw).** A seção da v1.36 estava certa e ficou ruim de ler em dev: a área da tabela é `flex-1`, então a seção era empurrada para o rodapé com um vão enorme acima, e a lista rolava numa faixa de 176px mostrando duas ou três linhas por vez. E, conceitualmente, é uma lista de PESSOAS — pessoas moram em Usuários. Agora Configurações → Usuários tem duas abas SÓ para a plataforma: "Staff das organizações" (a tela de sempre) e "Administradores da plataforma" (tabela só-leitura com nome, e-mail, status e data; sem coluna de ações e sem "Novo Usuário", porque promover e despromover é pelo script e `PATCH /users/{id}` de linha de plataforma é 404), com a aba na URL (`?tab=plataforma`). O admin de organização não vê faixa de abas, e o deep link `?tab=plataforma` é ignorado em silêncio — a página em si ele pode ver, então não é AccessDenied; só a plataforma pode saber quem é plataforma. Backend intocado: `GET /organizations/platform-admins` fica onde está e a lista canônica segue 63/63. Duas coisas de mecânica: a página de Usuários virou wrapper server + client component (`usuarios/page.tsx` + `users-page.tsx`, como `organizacoes/`), porque `useSearchParams` exige `<Suspense>`; e o `scrollRegionLabel` da tabela é "Lista de administradores da plataforma", diferente do rótulo da aba, porque `getByRole` do Playwright casa por substring e o nome igual acertaria aba e região. A tela de Organizações voltou a ser só a tabela; o ajuste responsivo da v1.36 (`md:h-full`) ficou, porque é correto por si._

_Versão 1.37 — 21/09/2026. **Staff passa a mudar de organização por TRANSFERÊNCIA, e o plano deixou de listar isso como limite (task 86e3bvbfx).** Caso real: o Murilo, gerente da Hologram, vai para a Prospecta, e "apagar e recriar" é IMPOSSÍVEL — `users.email` é UNIQUE no sistema inteiro e `created_by` de `clients` e `reconciliation_sessions` é `ondelete=RESTRICT`. Nasceu `POST /api/v1/users/{id}/transfer` (`ManagePlatformDep`) e a ação "Transferir de organização" no editar usuário, só para a plataforma, num diálogo PRÓPRIO que abre depois de o editar fechar (dois diálogos do Radix empilhados marcam o fundo com aria-hidden). **Não é um PATCH de `organization_id`**: `client_assignments` e `user_client_favorites` são pares usuário x cliente, e trocar só a organização deixaria a pessoa como responsável de clientes da organização antiga. As regras estão na §4.8: 409 enquanto responsável de cliente ABERTO, colaborador e favoritos cross-org saem na mesma transação, cliente encerrado e histórico ficam, papel não muda, efeito no request seguinte. Evento `usuario_transferido_de_organizacao` só com IDs e contagens. A lista canônica foi de 62 para **63** (DETAIL_PK, na bateria com os três atacantes). Duas armadilhas de teste desta entrega: `db.expire_all()` num teste async expira os objetos da fixture e o próximo `.name` estoura `MissingGreenlet` — refresh de UM objeto; e duas sessões de pytest no MESMO banco de teste ao mesmo tempo (bateria em background + arquivo em primeiro plano) dão 139 erros de setup que parecem regressão — rodada de banco também é exclusiva._

_Versão 1.36 — 18/09/2026. **A plataforma não conseguia ver quem é plataforma, e isso era consequência de uma correção certa.** A task 2 do épico fechou um IDOR real fazendo `GET /users` filtrar `scope='system'` no próprio SELECT — o admin de uma organização não pode alcançar a conta da plataforma. O efeito colateral que ninguém decidiu: a plataforma também deixou de ver os pares dela, e o `users_count` de cada organização conta só o staff dela (usuário de plataforma tem `organization_id` nulo e fica fora de todo total). Depois da promoção das contas reais, essas pessoas simplesmente somem da tela de Usuários. Nasceu `GET /api/v1/organizations/platform-admins` (`ManagePlatformDep`, payload enxuto `{id, name, email, active, created_at}`, sem paginação) e a seção SÓ-LEITURA "Administradores da plataforma" na tela de Organizações. Ela é só-leitura por construção, e é por isso que não virou filtro na tela de Usuários: promover e despromover é pelo script (Q3) e `PATCH /users/{id}` de linha de plataforma é 404, então ali as ações da linha e o botão de criar teriam de ser escondidos caso a caso — ação que o servidor nega é defeito (§4.9). A lista canônica **não muda** (62/62): a rota entra em `NON_TENANT_ENDPOINTS`, que passou de 4 para **5** rotas de `/organizations`. **Três defeitos desta entrega só apareceram no browser, e a forma deles é o que vale guardar:** (1) o fetcher devolve o ARRAY, não o envelope, porque o `apiGet` desembrulha `{ data }` quando `data` é a chave única — e `apiGet<T>` é genérico, então o tipo errado compila limpo; (2) o `json()` do mock do e2e JÁ envelopa, e o envelope duplicado derrubou a página inteira com "Application error", que o Playwright reporta como `element(s) not found` (o diagnóstico está no `error-context.md`, não na mensagem); (3) a seção nova disputou altura com o `flex-1` da tabela e a espremeu para UMA linha em 390px, com o gate VERDE, porque `toBeVisible` não distingue "fora do scroller" de "visível" — quem pegou foi comparar o PNG com o da entrega anterior. Encher a viewport só vale enquanto a tela couber nela: abaixo de `md`, altura natural e quem rola é o `<main>`. A skill `front-gate` ganhou as três regras, mais o `!s.unexpected` no guard da receita de container (o `N passed` do reporter de lista aparece mesmo com falha: uma rodada imprimiu `230 passed` nos três temas com `unexpected: 4` no JSON)._

_Versão 1.35 — 18/09/2026. **A varredura de QA do épico de organizações (86e36ed4b) achou o primer afirmando o CONTRÁRIO do código, em dois lugares.** A §1 dizia "**Não é multi-tenant de BPOs** — é uso interno da Hologram": era verdade até 09/2026 e deixou de ser na PRIMEIRA migration do épico. A plataforma hospeda organizações, e a Hologram é a primeira delas, não a dona do sistema — um agent lendo só a §1 escreveria endpoint sem dimensão de organização, que é vazamento entre BPOs. A nota ⚠️ do topo dizia que "a Sprint 5 fechou **34/34** endpoints sensíveis": número de agosto, enquanto a §3.15 já dizia 62/62 desde a task 4 do épico — o arquivo se contradizia havia duas semanas. As duas frases agora dizem a lei atual, e a contagem vem com o `grep` ao lado, como já acontece na §3.15 e na §4.1. A §8 ganhou o registro de que a camada de organizações **não** foi sprint do hub, com o que ela deixou no código. E `apps/api/docs/endpoints-sensiveis-sprint5.md` foi regenerado: estava uma task atrasado, com as três linhas de `anomaly-types` ainda dizendo "escrita pela matriz" depois que a escrita virou só-plataforma na 86e36ed1d._

_Versão 1.34 — 18/09/2026. **O primeiro CI da onda 2 reprovou e achou um defeito de contraste que estava na `main` havia semanas.** O `develop → main` derrubou os jobs `web_a11y` do escuro e do Hologram: o botão destrutivo do diálogo "Suspender organização" media **3,95:1** (mínimo 4,5). A causa não é da task 7 nem da 6: `hover:bg-destructive/90` compõe o vermelho com a superfície por **alfa**, e nos temas escuros, onde o rótulo do destrutivo é quase preto (`0 0% 9%`), escurecer o fundo aproxima os dois. Medindo todos os hovers com alfa contra os tokens reais, o **badge** destrutivo reprovava nos TRÊS temas (4,49 / 3,31 / 3,53); linha de tabela e demais variantes passam com folga. Correção: nasceu `--destructive-hover`, **sólido**, nos três blocos (escurece no claro, clareia no escuro e no Hologram — 7,68 / 5,63 / 6,00), e botão e badge passaram a usá-lo. **A regra nova vale para todo hover:** cor mesclada não é token e nenhum teste a trava, então estado de hover pede token próprio e linha em `PAIRS` do `theme-contrast.test.ts` (que foi de 54 para 57 asserções). **Por que passou três vezes no gate local e só o CI pegou:** o axe só enxerga o `:hover` se o ponteiro estiver sobre o elemento no instante do scan, e o `.click()` anterior deixava o ponteiro numa coordenada que, em 390px, calhava de cair sobre o botão — poucos pixels de layout decidiam. O e2e passou a fazer `hover()` **explícito** antes do `analyze`; com o alfa de volta, ele reprova nas quatro combinações de viewport e projeto, e não em uma só. §7 e a skill `front-gate` atualizadas._

_Versão 1.33 — 18/09/2026. **As telas existentes ficaram cientes de organização e a onda 2 fechou (task 86e36ed1d, última do front do épico 86e36ec0q).** A mudança que não é de front: `MANAGE_ANOMALY_TYPES` passou de `_ADMINS` para `_PLATFORM_ONLY` — a taxonomia de anomalias é uma tabela GLOBAL do produto, e o admin de UMA organização editaria o vocabulário que as outras usam (D3 final). A célula e a tela mudaram na MESMA entrega, porque item de menu e rota consultam a mesma permissão: tirar a célula antes teria deixado botões que o servidor nega, e depois teria deixado a tela sem dono. `?include_inactive=true` virou silencioso para o admin, como já era para o gerente. No front, a plataforma ganhou **coluna + filtro de Organização** nas listas de clientes, usuários e categorias (server-side, via `?organizationId=` — a decisão é `resolve_organization_filter`) e **seletor de organização de destino** nos três formulários de criação, com uma fábrica de schema só (`organizationTargetField`) espelhando `resolve_organization_for_creation`: obrigatório para a plataforma, ausente para o staff. Criar e filtrar são assimétricos de propósito — a organização SUSPENSA aparece no filtro (os clientes dela existem) e não no seletor de criação (o backend responderia 409). O helper datado `canCreateWithoutOrganizationPicker` foi APAGADO com os três usos: as telas voltaram a perguntar só à matriz, e quem recuperou os botões de criar foi a PLATAFORMA (o gerente nunca os perdeu — o helper só excluía escopo de plataforma). Dois defeitos latentes caíram junto: o badge de papel era um ternário `isAdmin ? 'Admin' : 'Gerente'` (o `else` rotularia qualquer papel novo como gerente) e virou `Record` exaustivo sobre a whitelist do contrato; e a seção "Gerentes com acesso" filtrava `role === 'manager'` no navegador sobre a primeira página de `/users` — com N organizações ofereceria gerente de outra org, que o backend recusa com o MESMO 400 de "não é gerente" (anti-enumeração), então passou a perguntar `?role=manager&organizationId=<org do cliente>`. Copy neutra em 6 strings de tela de DADO; login, header, tema e logomark não mudaram (D4). §4.9 atualizada: a linha de tipos de anomalia perdeu o "(\*)" e o admin perdeu a célula._

_Versão 1.32 — 18/09/2026. **O front aprendeu a camada de organizações (task 86e36ecwa, onda 2 do épico 86e36ec0q).** O contrato foi regenerado e o `Record<UserRole, …>` de `lib/authz.ts` quebrou a compilação até a matriz ganhar a coluna `platform_admin` — a armadilha desejada. O espelho do front virou 13 × 5, com `isStaff`, `canAccessClient` liberando a plataforma, `canManageSystemUsers` consultando `manage_org_users` e `organizationLabel` ("Plataforma" ou o nome da organização, que o header agora mostra ao lado do papel). Nasceu `/configuracoes/organizacoes` (só `manage_platform`): lista paginada com as duas contagens, criar, renomear e suspender/reativar, com a consequência dita ANTES de confirmar. A seção Configurações do menu passou a ser montada item a item pela matriz. ⚠️ **Três testes que gravavam a regra ANTIGA foram corrigidos**, não a regra: a D2 (86e36ecjp, na `main` desde 17/09) deu ao gerente da organização a célula `manage_client_users`, então "Usuários" dentro do cliente passa a aparecer para ele — o espelho do front seguia dizendo que não. §4.9 atualizada; âncoras da skill `front-gate` recolhidas._

_Versão 1.31 — 17/09/2026. **As rotas existentes ficaram org-aware e a onda 1 fechou (task 86e36ecqz, última do back do épico 86e36ec0q).** `GET /users` ganhou `?organizationId=` e `?role=` e passou a dizer a organização de cada staff (`scope`, `organization_id`, `organization_name`); `POST /users` aceita `organization_id` só da plataforma (obrigatório para ela; o admin cria na própria e payload divergente é 403); `GET /clients` aceita `?organizationId=` e cada cliente traz `organization {id, name}`; a categoria de um cliente é validada no catálogo DA org dele (outra org = o mesmo 400 de inexistente); o catálogo de categorias virou por organização de ponta a ponta (leitura pela org da LINHA, plataforma todas, escrita na org do ator, alvo por PK alheio = 404, unicidade por org) e as 4 rotas saíram de `PENDING_ENDPOINTS` — cobertura 62/62. Duas decisões novas e únicas em `authz.py`: `resolve_organization_for_creation` e `resolve_organization_filter`. O rótulo de autoria virou "Equipe {org do cliente}" (a org vem de `CurrentUser.organization_name`; a Hologram segue "Equipe Hologram"). A sessão por PK sai do `SELECT` restrita ao ALCANCE (`scoped_by_reach`): o admin/gerente de outra organização nem carrega a linha, e `audit_session_tenant_miss` pergunta a `resolve_client_access` e grava a negação. Nasceu `scripts/promote_platform_admin.py` (por e-mail, idempotente, recusa tenant, `--dry-run`) — o único caminho para `platform_admin`. §3.15 e §4.8 atualizadas. **Em dev nada muda de visível** enquanto só a Hologram existir; a promoção dos cinco só depois da onda 2._

_Versão 1.30 — 17/09/2026. **Nasceu o módulo de organizações e a bateria cross-org (task 86e36ecnp, onda 1 do épico 86e36ec0q).** `GET/POST /api/v1/organizations` e `GET/PATCH /api/v1/organizations/{id}` (só `platform_admin`; nome único sem caixa; `active=false` suspende: o staff da organização recebe 401 no request seguinte, o login é recusado com a mensagem genérica e a plataforma não cria cliente nela; reativar desfaz), com os eventos `organizacao_criada` e `organizacao_desativada` (só IDs e contagens, sem dedup). A lista canônica passou de **49 para 62**: `/users` (6), `/clients` GET/POST e `PATCH /clients/{id}` (3) e `/client-categories` (4, em `PENDING_ENDPOINTS` até a 86e36ecqz) saíram de "não-sensível" — eram "admin-only global", e admin agora é de UMA organização. A bateria ganhou a dimensão de ORGANIZAÇÃO: cada endpoint é disparado por três atacantes (operador de outro tenant, admin e gerente de outra organização), e a asserção passou a cobrir também o nome de um staff da Hologram. §3.15 atualizada com a contagem, o comando e o critério de "escopável"._

_Versão 1.29 — 16/09/2026. **D2 entrou (task 86e36ecjp, onda 1 do épico 86e36ec0q): o `manager` gere os usuários dos clientes da carteira, e a carteira virou intra-org.** A célula `manage_client_users` ganhou o `manager` (o alcance segue sendo `resolve_client_access`, então fora da carteira continua negado); `is_active_manager` exige gerente da mesma organização do cliente e é a única validação de criar cliente, adicionar gerente e definir responsável (400 único, anti-enumeração); `POST /clients` decide a organização pela LINHA do ator — staff na própria (payload divergente é 403), plataforma escolhe (obrigatório; 404 inexistente, 409 suspensa) — e o criador gerente vira responsável só se for da org do cliente. §4.9 e §4.13 atualizadas. ⚠️ Efeito visível para os managers da Hologram no deploy da onda 1: as seis rotas de usuários do cliente passam a responder para quem tem o cliente na carteira; a aba na tela chega na onda 2._

_Versão 1.28 — 16/09/2026. **O authz core da camada de organizações entrou (task 86e36ecar, onda 1 do épico 86e36ec0q) — a linha mais perigosa da sprint.** `UserScope.PLATFORM`/`UserRole.PLATFORM_ADMIN` existem; `resolve_client_access` tem uma ordem nova (cliente → plataforma bem formada libera → staff só alcança cliente **da própria organização**, admin a org inteira e manager a carteira dentro dela); nasceram `reach_filter`/`scoped_by_reach` (a decisão projetada em `WHERE` para coleções por `client_id`) e `scoped_by_organization`; a matriz virou 13 x 5 com a plataforma em toda linha e um teste que trava isso. As **4 cópias** da regra "admin vê tudo" fora do `authz.py` (notificações, tipos de anomalia, lista de clientes, criação de conciliação) e os guards por string `require_admin`/`require_manager_or_admin` **deixaram de existir**: toda rota usa guard da matriz ou `StaffDep`. `get_current_user` e o login leem a organização junto com o usuário e recusam organização suspensa; o JWT e o corpo do login/refresh carregam `organization_id`/`organization_name`; `access_audit` grava `actor_organization_id` (a telemetria mantém as 4 props da S5). Num mundo de uma organização só, **nada muda de visível**. §3.15 e §4.9 reescritas como lei atual._

_Versão 1.27 — 16/09/2026. **A fundação de dados da camada de organizações entrou (task 86e36ec7p, onda 1 do épico 86e36ec0q).** Tabela `organizations` com a Hologram de id fixo, `organization_id` em `clients` (NOT NULL), `users` (nullable: a plataforma não tem org) e `client_categories` (UNIQUE passou a `(organization_id, name)`), `access_audit.actor_organization_id`, e o CHECK de `users` virou ternário e cruza o papel (`ck_users_scope_consistency` no lugar de `ck_users_scope_client_id`). O backfill "tudo é Hologram" é por catálogo (`server_default`, como o `is_primary` da carteira), a migration pré-checa o CHECK em plpgsql antes de trocá-lo e o downgrade aborta se houver segunda organização ou usuário de plataforma. **Nenhum comportamento de API muda** nesta task: é o canary da migration antes do authz core. §4.8 reescrita como lei atual; o resto da camada (regra de acesso, matriz 13 × 5, rotas, telas) chega nas tasks seguintes e atualiza §3.15 e §4.9 então. Plano: `Docs/PLANO_ORGANIZACOES.md`._

_Versão 1.26 — 16/09/2026. **Dentro da passada de data, os PARES fecham em ordem de evidência, não linha a linha (task 86e39p1wv, report da Bruna de 15/09).** O caso: dois PIX de mesmo valor no mesmo dia; a descrição de um trazia só uma sigla de 2 letras (a afinidade descarta tokens curtos, e o cadastro Omie traz o nome da pessoa), então essa linha tinha afinidade zero com os DOIS lançamentos, enquanto a outra tinha dois tokens em comum com o dela. O `match()` decidia linha a linha em ordem `(data, id)`, e id é UUID: quando a linha sem sinal vinha antes, levava o lançamento da outra pela ordem da lista, e a IA acusava incoerência nas duas. Cara ou coroa por sessão, e por isso "às vezes funcionava". Agora cada passada monta todos os pares possíveis e fecha do mais forte para o mais fraco (`|Δvalor|`, afinidade, data, ordem da linha, posição na lista). **As leis da §5 não mudaram**: 0,01, 3 dias fixos, 1-para-1, passadas por data, sem IA, nome nunca exclui; para cada linha o par continua sendo o melhor candidato livre DELA no momento em que fecha, muda só QUEM decide primeiro. Medido em 20 mil cenários sintéticos (`apps/api/scripts/measure_matcher_evidence_order.py`, versionado, com o algoritmo antigo embutido como referência): pares de data exata idênticos, 32 pares a mais, 684 pares com a PESSOA errada a menos (4.554 para 3.870), 391 cenários corrigidos contra 4 introduzidos (evidência fraca vencendo linha sem sinal — antes era sorteio de UUID). `TieStats` ganhou `steals_prevented_by_supplier`, logado em `reconciliation_matched`: é o sinal de produção desta correção, porque o conjunto de candidatos não persiste. Skill `matcher` atualizada (invariantes 5 e 6, números de linha)._

_Versão 1.25 — 15/09/2026. **A carteira deixou de ser exclusiva: N gerentes com acesso, UM responsável (épico 86e390kku, task 86e390kz8) — nova regra §4.13.** Origem: ao tornar o Murilo gerente do cliente Hologram, a Bruna perdeu o acesso na hora, sem aviso — não era bug de operação, era o modelo (`UNIQUE(client_id)` + "reatribuir" sobrescrevendo o `user_id`). Agora `client_assignments` tem `is_primary`, `UNIQUE(client_id, user_id)` e o índice único parcial do responsável (migration `6bb85e6b7d72`, backfill "todo gerente existente vira responsável", downgrade que ABORTA se houver colaborador). Três rotas novas (`GET/POST /clients/{id}/managers`, `DELETE .../managers/{user_id}`) e `PATCH /assign` re-semantizada para "definir responsável, sem remover ninguém"; as quatro entraram na lista canônica como DETAIL_PK (**49**, não 45 — e `/assign` saiu de `NON_TENANT_ENDPOINTS`). Duas armadilhas fora do escopo original da task ficaram registradas em código: o `get_assignment(client_id)` do repositório também usava `scalar_one_or_none` (substituído por leitores por par e por responsável), e o join de exibição da lista era o MESMO usado no filtro da carteira (separados: join só do responsável, filtro por `EXISTS`). §3.11 reescrito para "fora da própria carteira"._

_Versão 1.24 — 15/09/2026. **O QA do épico de skills achou a §3.15 desatualizada em 1 endpoint, e a contagem agora vem com o comando que a confere.** A lista canônica tem **45** entradas desde a rota `close` (Sprint 6, 86e36pm1z); a §3.15 ainda dizia 44, enquanto o rodapé da v1.21 já dizia 45 — o primer se contradizia havia cinco dias. A correção não é só o número: a §3.15 passa a citar o `grep` que responde a pergunta no arquivo, do mesmo jeito que a §4.1 passou a apontar para as constantes de AAD na v1.22. Número solto envelhece calado; número com comando ao lado é conferível em dez segundos. O resto da varredura passou: **152 âncoras de arquivo e linha e 413 identificadores** das nove skills conferidos contra o código, com **um** caminho errado (dois componentes de revisão sem o segmento `reconciliations/`, corrigidos na `front-gate`). Nenhuma outra seção mudou._

_Versão 1.23 — 15/09/2026. **O primer devolveu ao dono o que era procedimento (86e2ufky2, fecho do épico de skills).** Com as nove skills na `main`, quatro blocos que descreviam COMO fazer viraram ponteiro para quem agora os detalha: a §7 Frontend inteira e o bloco de CI/CD verde (skills `front-gate` e `gate`), a §9 de comandos (`gate`, com `migration` e `sprint-preflight` ao lado) e o roteiro de fechamento da §12 (`entrega`). A §5 perdeu a narrativa do cruzamento e a nomenclatura do Omie (skills `matcher` e `omie`) mas **manteve as leis numéricas** — 0,01 BRL, os 3 dias fixos, o período expandido, o 1-para-1, a idempotência e o "IA nunca decide match" — porque são violáveis por quem nunca abre o matcher, escrevendo um endpoint ou uma tela. Resultado: **759 para 665 linhas, 77.748 para 68.959 bytes**, 11% a menos em toda sessão. **§3 e §4 estão byte a byte idênticas**, e a §6 só ganhou dois ponteiros: são invariantes e conduta, que precisam valer sem gatilho nenhum. Duas decisões do Pedro no caminho: o rodapé de versões fica no arquivo (é 26% dele, mas serve de contexto recente) e a §6 não é condensada. Antes de cortar, quatro regras órfãs foram para as skills que passaram a hospedá-las — `noUncheckedIndexedAccess` e server component por padrão na `front-gate`; teste flaky, hooks locais com a proibição do `--no-verify` e os comandos de banco na `gate` — porque mover regra antes de existir destino é perder a regra._

_Versão 1.22 — 14/09/2026. **A lista de campos cifrados da §4.1 estava DESATUALIZADA em 4 campos, e agora aponta para a fonte executável.** A varredura da skill `crypto-field` (86e2ufkvr) comparou o primer com o código: o `crypto_service.py` declara **11** constantes de AAD e os modelos têm **11** colunas cifradas, enquanto a §4.1 listava **7**. Faltavam a do nome de arquivo em `reconciliation_files` (Sprint 4) e as três do glossário em `client_glossary_entries` (Sprint 6): cifradas no código desde que nasceram, ausentes do primer desde então. A correção não é só somar as quatro. A §4.1 passa a declarar que **a fonte única é o bloco de constantes de AAD do `crypto_service.py`**, e que os pares (tabela, coluna) são congelados, porque renomear um invalida a decifragem do que já foi gravado. Assim a próxima dessincronização tem um lugar verificável para ser pega: a contagem dessas constantes contra a lista daqui._

_Versão 1.21 — 10/09/2026. **Nasceu o segundo modo de saída de cliente: ENCERRAMENTO com retenção (86e36pm1z) — nova regra §4.12.** Direção do Lucas (09/09): a exclusão total perdia "informação valiosa"; o encerramento apaga a identificação e os sensíveis e mantém o operacional. Mecânica: `clients.closed_at` (migration `c9e4a7b2d5f8`), nome → "Cliente encerrado #hex8", credenciais Omie vazias, **`dek_wrapped` → NULL (crypto-shredding — o provisionamento lazy de DEK é o landmine: o guard bloqueia escrita ANTES do `ensure`)**, usuários do tenant anonimizados + desativados (FK RESTRICT das sessões impede apagar), glossário/cache/notificações/favoritos purgados; conciliações, carteira e trilhas ficam. Trava única de rota: `OpenClientDep` em TODA escrita de cliente (update, sync, usuários, glossário, conciliação nova, posting); leitura segue `AccessibleClientDep` — e o detalhe de encerrado NÃO fala com o Omie (o miss do cache tentava decifrar credencial vazia e dava 500, pego por teste). Lista canônica: **45** endpoints (a rota `close` entrou como DETAIL_PK). Terminal: cliente que volta é cadastro novo; exclusão total continua valendo para encerrado (LGPD). Evento `cliente_encerrado` (sem dedup, como todo evento novo)._

_Versão 1.20 — 03/09/2026. **A coluna Fornecedor das divergências de título deixou de ser "—" estrutural (86e33bmkb, fecho do épico).** `reconciliation_omie_entries` ganhou `supplier_code` (migration `b7d4e91c2a53` — código numérico do cadastro, em claro, mesma classe do `category_code`), preenchido pelo job a partir de `nCodCliente` (extrato) / `codigo_cliente_fornecedor` (títulos). O NOME resolve em runtime: novo `OmieClient.consultar_cliente` (`ConsultarCliente` de `geral/clientes` — request da família já rodava em prod na validação de credencial; campos de response da doc oficial, captura opcional via `OMIE_CAPTURE_CLIENTE_CODIGO`) + `clientes_cache` (TTL 6 h + negativo 15 min p/ fault, chave por tenant), consumido fail-soft pela listagem/PATCH da revisão. Fault do Omie (código excluído) marca negativo; falha de transporte nunca marca. §4.5 atualizada: o delta "código não é nome" agora cobre os três campos do snapshot. Export segue mostrando só o código (sem resolução de nome — decisão de escopo). Linhas pré-migration continuam "—" (sem backfill, mesmo racional das colunas irmãs)._

_Versão 1.19 — 02/09/2026. **A aba Divergências Omie ganhou snapshot do processamento (86e33bmkb) — e a §4.5 ganhou o delta "código não é nome".** O bug do "—" perpétuo tinha duas causas provadas em log de dev: colisão de `ListarExtrato` concorrente da própria Tela de Revisão (o Omie processa 1 requisição por método por app_key) e o código `8020` — gêmeo do `1880` no endpoint de extrato — fora da lista retryable, virando `OmieFaultError` permanente sem retry (corrigidos em `ed0f8aa`: `8020` retryable + lock por cliente no `populate_from_extrato`). O que restava eram os TÍTULOS (Atrasado/Previsto), invisíveis ao `ListarExtrato` por natureza: agora `reconciliation_omie_entries` persiste `amount` (em claro, §4.3) e `category_code` (só o código) na criação da linha (migration `a3f8c21d9b47`), a listagem/PATCH da revisão e o export usam o snapshot como fallback, e a descrição da categoria é resolvida em runtime via `ListarCategorias` cacheado — fornecedor de título segue "—" (a API do Omie devolve só o código do cliente, §5.5). De quebra, as properties de exibição do `LancamentoExtrato` desfazem entidades HTML que o Omie devolve em texto livre (`&gt;&gt;` aparecia cru na UI)._

_Versão 1.18 — 25/08/2026. **O relatório do gate de a11y mudou de endereço (86e2w8xpv)**: `scripts/a11y-gate.sh` e o `web_a11y` do CI passam a gravar `apps/web/test-results/a11y-report-<tema>.json` — dentro do diretório que o `.gitignore` da RAIZ já ignora, fechando de vez a classe "artefato do gate entra em commit" (a regra na raiz nunca sobreviveria a uma sprint: o arquivo está fora do `gitPaths` de todos os papéis do hub). O `apps/web/.gitignore` vira cinto para script antigo. §7 atualizado com o efeito colateral aceito: o Playwright limpa `test-results/` a cada run — só o relatório do último tema sobrevive ao gate, o guard lê cada um logo após o próprio run, e os screenshots seguem fora (`a11y-shots/<tema>/`)._

_Versão 1.17 — 25/08/2026. **O tema Hologram virou o PADRÃO do produto** (decisão do Pedro, sem task — registro direto). `defaultTheme="hologram"` no provider raiz: quem nunca escolheu tema vê a marca por padrão, inclusive no login; escolha salva no localStorage é respeitada (o next-themes só grava no `setTheme`), e Claro/Escuro/Seguir o sistema continuam no toggle — `system` virou escolha, não padrão. §7 atualizado; o default ganhou trava própria no e2e (localStorage vazio → `<html class="hologram">`, nas três legs do gate). O ícone pré-hidratação do toggle passou a ser a logomark (o caso comum agora é Hologram)._

_Versão 1.16 — 25/08/2026. **A marca aterrissou (86e2ukrc9): paleta nos tokens, tema HOLOGRAM e a logomark no produto.** O marinho oficial (`#0C0C5A`, amostrado por PIXEL do logomark em `Docs/brand/` — nunca de print, §6.5) entrou em `--primary`/`--ring`/`--accent` dos temas claro/escuro, e nasceu o TERCEIRO tema `.hologram` (superfícies navy da marca, botão primário branco; `system` não o resolve — escolha manual). A logomark vive em `components/shared/brand-mark.tsx` como CSS mask + `bg-current` (um PNG de 5KB, cor por token, sem variante por tema) no header e no login — e abaixo de `sm` a logo É a marca (o título some; com os dois, o truncate esmagava o título para um "A" órfão em 390px, pego por print). §7 atualizado: o gate roda EM TODOS os temas (matrix de 3) e o `theme-contrast.test.ts` asserta os três blocos (54 pares). Turquesa/azul-royal ficaram FORA por falta de referência-fonte — retomar se o guia da marca aparecer._

_Versão 1.15 — 25/08/2026. **O sistema ganhou tema claro/escuro (86e2n39hb) — e duas regras novas no §7.** O `ThemeProvider` do next-themes subiu no layout RAIZ (tema vale no login; padrão `system`, escolha no localStorage — decisões do Pedro em 25/08), com toggle no header. A varredura matou TODA cor fixa da paleta e TODA variante `dark:` em componente (13 arquivos migrados para os tokens semânticos que o `globals.css` já tinha nos dois temas; âmbar e laranja colapsaram no MESMO `warning` de propósito — eram vizinhos indistinguíveis e o rótulo sempre foi o distintivo). Regra nova: cor em componente é SEMPRE token semântico, com o grep de cor fixa em zero como critério verificável — é o contrato que a task da paleta (86e2ukrc9) herda: ela muda só VALORES no `globals.css`. Segunda regra: **o gate de a11y roda nos dois temas por mecanismo** (`A11Y_THEME` no script, matrix no CI) — o gate pegou de verdade nesta task (`aria-hidden-focus` no menu de tema em modo modal, corrigido com `modal={false}` como no sino). Detalhe de mecanismo: os screenshots do gate saem em `a11y-shots/<tema>/`, FORA de `test-results/` — o Playwright limpa aquele diretório a cada run e o segundo tema apagava a coleção do primeiro._

_Versão 1.14 — 21/08/2026. **A captura S-1 aconteceu — o contrato de escrita do Omie deixou de ser suposição.** Rodada contra a conta real da Hologram (cartão Inter, três iterações guiadas por faultstring): o formato PLANO foi recusado (`5001`), o aninhado com `nValorLanc` string e sem `cTipo` caiu em `3102`, e o aninhado com **número JSON + `cTipo='DIN'`** foi ACEITO. **§3.16 reescrita como lei atual:** contrato verificado (aninhado, sem `cNatureza` na escrita, valor absoluto → débito), **idempotência de `cCodIntLanc` confirmada** (2º POST devolve o mesmo `nCodLanc`), **estorno bloqueado** (`estorno_nao_verificado`) até a representação do crédito ser capturada, e o caminho pós-timeout registrado como sempre-inconclusivo (o extrato NÃO devolve `cCodIntLanc`) com o reenvio idempotente como decisão em aberto (§10). **§5.6–5.7** ganharam as duas descobertas de leitura: extrato de CARTÃO usa natureza `P`/`R` com valor JÁ sinalizado (`signed_amount` cobre as duas convenções — só inverte `'D'`), e lançamento recém-criado volta **sem `cSituacao`** (campo agora opcional; exigi-lo derrubava o reprocessamento no dia de uma inclusão). Fixtures reais anonimizadas entraram em `apps/api/tests/fixtures/omie/` e o gate `test_omie_fixtures.py` roda verde contra elas — inclusive as 5 leituras, validadas contra resposta real pela primeira vez._

_Versão 1.13 — 22/08/2026. **O épico "Tela de revisão: confiança no que a tela mostra" (86e2n4tck) fechou 7/7 e deixou três regras novas.** **§3.15** ganhou a regra de identidade em response: autoria exposta é `{name, email}` mascarada por escopo via `author_for_viewer` — usuário de tenant vê "Equipe Hologram" para autor da equipe, e a máscara é do servidor. **§3.16** registra o cross-check da doc do `IncluirLancCC` (19/08): o `param` documentado é ANINHADO e sem `cNatureza` — o DTO plano não foi reescrito de propósito, e a recusa do 1º POST da captura passou a ser desfecho esperado e evidência válida. **§7** ganhou: tooltip nunca é `title` nativo (padrão `role="img"` + `aria-label` + `tabIndex`, três componentes já o usam); "visível não é estável" agora cobre COR (o axe medindo toast em fade reprova tokens que passam — `aguardarToastEstavel`); teste de integração novo roda na ordem do CI com seed get-or-create; e o parágrafo do `a11y-report.json` foi atualizado — a lacuna do `.gitignore` fechou na Sprint 7 (`apps/web/.gitignore`). Somas da aba Resumo, filtros server-side e o rótulo "Conciliadas (data exata)" são entregas do épico registradas nos PRs #79–#93, não regras novas — o que era regra ("fonte única de contadores", §5 intocada) só foi reafirmado._

_Versão 1.12 — 18/08/2026. **O ADL passou a ESCREVER no Omie (Sprint 7) — o invariante "Omie read-only" acabou, e essa é a mudança mais perigosa que este primer já registrou.** Nova regra **§3.16** com o que não pode ser errado: a feature nasce **desligada** (`OMIE_POSTING_ENABLED=false` por default, ao contrário de todo outro flag do projeto); o contrato do `IncluirLancCC` segue **NÃO-VERIFICADO** contra a API real (S-1) e o gate `tests/unit/test_omie_fixtures.py` **SKIPA citando S-1** em vez de passar verde; a **dedup primária é do ADL** (`reconciliation_omie_postings`, §4.11), nunca do fornecedor; `cCodIntLanc` vem da **identidade da linha**, nunca do conteúdo — chave de conteúdo colapsaria duas compras idênticas e deixaria dinheiro **faltando**, que o rollback não vigia; timeout **reconcilia antes de reenviar** e inconclusivo **não reenvia**; `faultstring` nunca é logada. Corrigido o número da lista canônica de endpoints sensíveis (**40**, não 34 — a Sprint 6 e a 7 entraram e o primer não acompanhou). **§8** ganhou o que a Sprint 6 (glossário) e a 7 deixaram no código, e **§10** marca a quebra do invariante como decidida, com a captura da fixture real explicitamente **ainda pendente**._

_Versão 1.11 — 11/08/2026. **O alerta sintético ganhou canal próprio (§3.14).** O gate de deploy dispara `AlertCode.SYNTHETIC` a cada push na `main` — prova de entrega, não incidente — e isso caía no canal de plantão várias vezes por dia. Com `ALERT_WEBHOOK_URL_SYNTHETIC` configurada, o sintético (e só ele) sai por um webhook separado, sem e-mail de plantão junto; sem a setting, nada muda. A parte que não pode ser errada: essa URL **não** conta em `has_webhook_alert`/`has_alert_channel` — canal de teste não substitui canal de plantão, e contá-la faria o fail-closed deixar subir um serviço mudo para alerta real._

_Versão 1.10 — 08/08/2026. **O matcher ganhou uma terceira dimensão: fornecedor (§5.5).** Ele conhecia valor e data, e mais nada — era o que sustentava a frase da Bruna de que o sistema cruzou o extrato de um fornecedor com o lançamento de outro "considerando apenas o valor". O dado existia e era descartado na montagem: `LancamentoExtrato.supplier` nunca chegava ao `OmieMovement`, e a descrição do arquivo nunca chegava ao `FileEntryForMatch`. Agora chegam, e a afinidade entre os dois entra como desempate **depois** do valor (valor é fato, nome é indício) e **nunca** como exclusão. A métrica é contagem de tokens em comum — sem limiar arbitrário para justificar quando um cruzamento sair errado — e continua determinística, sem IA (§5.9). Títulos a pagar/receber ficam de fora por limitação do Omie, que devolve só o código do cliente. `MatchResult` passou a carregar `tie_stats` (contadores puros, sem PII) porque o conjunto de candidatos de um cruzamento **não é persistido** e essa pergunta não tem resposta retroativa no banco._

_Versão 1.9 — 07/08/2026. **O cruzamento deixou de ser guloso na ordem do arquivo (§5.5).** Passa a acontecer em **passadas por `|days_diff|` crescente**: todos os pares de data exata primeiro, depois 1, 2 e 3 dias. Motivo: uma linha cuja contraparte não casa por valor (pagamento **dividido** em duas parcelas no Omie, contra um cruzamento 1-para-1) levava o lançamento de outra linha dentro dos 3 dias; a linha roubada virava `sem_omie` e a qualificação acusava incoerência na primeira — **um pareamento errado, duas anomalias falsas**. Reportado pela Bruna em 04/08/2026 no cliente Romilson Carpintaria. As tolerâncias não mudaram (`AMOUNT_TOLERANCE = 0.01`, `DATE_DIVERGENCE_RANGE = 3`), o cruzamento continua 1-para-1 (§5.4) e continua determinístico, sem heurística e sem IA (§5.9). Efeito colateral desejado: **a ordem de leitura do arquivo não afeta mais o resultado** — as linhas decidem em `(transaction_date, id)`._

_Versão 1.8 — 07/08/2026. **Idioma de commit, branch e PR fixado na §7.** Commit e nome de branch passam a ser escritos em **inglês (EN-US)** — o formato Conventional Commits não muda, só o idioma do texto; título e corpo de **PR continuam em português**. A seção Idioma cobria código, comentários e mensagens ao usuário final, mas era silenciosa sobre os artefatos de git, e o silêncio vinha sendo lido como "tudo em português". Vale a partir desta data, sem reescrever histórico._

_Versão 1.7 — 03/08/2026. **Sprints 4 e 5 aterrissaram na `main`; o primer estava duas sprints atrasado.** Antes desta revisão o arquivo não continha uma única menção a `tenancy`, `scope`, `client_manager` ou `PERMISSION_MATRIX` — um agent lendo o primer partiria da premissa de que só existem os papéis `admin`/`manager` e escreveria query sem filtro de tenant, reabrindo a classe de vazamento que a Sprint 5 fechou em 34/34 endpoints. **Nova regra §3.15** (autorização por tenant: decisão vem da LINHA, `resolve_client_access` é a função ÚNICA, `scoped_by_tenant` na camada de dados, endpoint novo entra em `sensitive_endpoints.py` com teste negativo). **Novas regras §4.8–§4.10**: modelo de tenancy em `users` com o CHECK `ck_users_scope_client_id`; matriz de permissões declarativa (`core/authz.py` no back, `lib/authz.ts` no front); uma conciliação = conta + mês, com o hash em `reconciliation_files`. **§4.7** ganhou `user_scope`/`actor_client_id` na `access_audit`. Nota de status reescrita com o que cada sprint entregou, **§8** ganhou o mapa das sprints do agents-hub. Corrigidas duas afirmações falsas: a Sprint 3 **está** na `main` (não "aguardando review") e a FASE 1 **está** na `main` (`processing/matcher.py`), não numa branch de integração._

_Versão 1.6 — 22/07/2026. **Sprint 3 (Cripto por cliente, auditoria de acesso e alerta) — na `develop`, PR #38 aguardando review p/ `main`:** §3 e §4 atualizados como lei atual. Cripto migrou para **envelope com DEK por cliente**: cada cliente tem uma DEK própria embrulhada em `clients.dek_wrapped`; a KEK faz wrap/unwrap (**Cloud KMS** em staging/prod via `KEK_KMS_KEY_NAME`, **wrapper local** derivado de `OMIE_ENCRYPTION_KEY`/HKDF em dev/test); AAD liga o ciphertext ao cliente. Nova regra §3.14 com os **landmines** (nunca trocar `KEK_KEY_ID`=`k1`, manter `OMIE_ENCRYPTION_KEY`, não alternar KMS⇄local sobre DB com DEKs, alerting fail-closed em prod/staging). Nova regra §4.7: trilha `access_audit` (LGPD — {denied,view,export}, só IDs). Migrations aditivas (`dek_wrapped`, `access_audit`) reversíveis; endpoint `POST /system/alert-test` admin-only. ⚠️ Deploy exige provisionamento GCP prévio (`scripts/setup-gcp.sh <env>`: KEK no KMS + secrets de alerta + IAM) e backfill das DEKs pós-deploy — ver `scripts/environments-runbook.md`._

_Versão 1.5 — 19/06/2026. **FASE 1 / BACK 1.6 (tolerância de data fixa) na branch de integração `feat/fase1-cartao`:** a tolerância deixou de ser parametrizável — `DATE_DIVERGENCE_RANGE = 3` fixo no matcher. Classificação: data exata → `conciliado`; 1–3 dias → `conciliado_data_divergente` (+ `wrong_date`); > 3 → `sem_omie`. Vale para CC **e** cartão (muda comportamento da CC em prod quando a FASE 1 for mergeada). §5.2/§5.3 reescritos como lei atual, nota do topo e ponto em aberto §10 marcados resolvidos. `date_tolerance_days` removido do request (ignorado se enviado); coluna mantida (novas sessões = 0). O range fixo também rege a janela Omie no processamento, na revisão e no export. Gate local verde (ruff/mypy/pytest — matcher 20, + regressão CC e divergência no job). **Ainda não na `main`** (integração)._

_Versão 1.4 — 16/06/2026. **FASE 0 / S20 (BACK 0.1) aterrissou:** Redis/ARQ removido — background jobs agora via `BackgroundTasks` nativo do FastAPI; cache de lançamentos virou **L1-only** (L2 Redis existia só p/ coerência com o worker separado, que não existe mais). §2 (stack/infra), §8 (mapa S10/S11), §9 (comandos) e §10 (job runner) atualizados como lei atual. Teto do processamento via `asyncio.timeout(RECONCILIATION_TIMEOUT_SECONDS=900)` + cron de cleanup como rede de segurança. Gate local verde (ruff/mypy/507 pytest). §5 (tolerância de data) **não** alterado de propósito — muda só na FASE 1. ⚠️ Cloud Run da API exige `--no-cpu-throttling` + `min-instances ≥ 1` (§10)._

_Versão 1.3 — 15/06/2026. Reordenação do roadmap pelo PRD de 15/06 (FASE 0–5). Status (§ topo) e Fontes da Verdade (§1) passam a apontar o plano vigente [Docs/PLANO_PROXIMOS_PASSOS.md](Docs/PLANO_PROXIMOS_PASSOS.md); o [PLANO_S20_AUDITORIA_CONTINUA.md](Docs/PLANO_S20_AUDITORIA_CONTINUA.md) foi marcado **superseded** (absorvido na FASE 5). §10 ganhou os pontos em aberto do PRD. Os 2 bugs da FASE 0 já estavam resolvidos (auth #19, timeout #16)._

_Versão 1.2 — 11/06/2026. Adicionada a obrigação contínua de manter este primer atualizado (§13, parte do Definition of Done). Varredura de stack contra o código: corrigido o formatter (`ruff format`, não black) em §2/§7, completada a lista de regras ruff (`+ S, A, ASYNC, ANN, PT, TID`), FastAPI alinhado para 0.115+ e `hypothesis` incluído no conjunto de testes. Status S0–S19 em dev e eixo S20+ revalidados contra git log e estrutura de `apps/api`._

_Versão 1.1 — 09/06/2026. Atualizado o status (S0–S19 em dev), worker (ARQ, não Celery), deploy (Google Cloud Run/GCP) e o eixo S20+. Alinhado à documentação em `Docs/documentation/`, ao plano em `Docs/PLANO_IMPLEMENTACAO.md` e ao pivot em `Docs/PLANO_S20_AUDITORIA_CONTINUA.md`._
