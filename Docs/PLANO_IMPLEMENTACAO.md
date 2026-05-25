# Plano de Implementação — Sistema de Auditoria de Lançamentos (Hologram)

> **Versão 2.0 — reescrita completa em 24/04/2026** alinhando stack (Python + Next.js) à documentação oficial em `Docs/documentation/` e ao backlog em `Docs/List _ Auditora de Lançamentos - Backlog _ Hologram (Lista) - TAREFAS.pdf`.
>
> **Público-alvo:** time de desenvolvimento (back Python, front Next.js, IA/integrações, DevOps).
> **Escopo:** SaaS interno da Hologram Gestão para automatizar a conciliação bancária de clientes BPO cruzando extratos/faturas com o ERP Omie, usando IA (Claude) para extração e lógica determinística para matching.
>
> **Como usar este documento:**
>
> 1. Leia as seções **§1 a §10** uma única vez — são os fundamentos arquiteturais e estão congelados.
> 2. As **sessões de implementação (§11)** são o guia de trabalho. Cada sessão corresponde a uma ou mais conversas com o Claude e cobre um conjunto coeso de tarefas do backlog.
> 3. Antes de cada sessão, verifique o checklist de **pré-requisitos** e garanta que as sessões anteriores estão concluídas.
> 4. Ao concluir uma sessão, marque o **Definition of Done** e atualize o [CLAUDE.md](../CLAUDE.md) na raiz.

---

## Sumário

1. [Visão Geral do Sistema](#1-visão-geral-do-sistema)
2. [Stack Tecnológico Definitivo](#2-stack-tecnológico-definitivo)
3. [Arquitetura de Alto Nível](#3-arquitetura-de-alto-nível)
4. [Modelo de Dados](#4-modelo-de-dados)
5. [Segurança — Regras Invioláveis](#5-segurança--regras-invioláveis)
6. [Análise de Escalabilidade](#6-análise-de-escalabilidade)
7. [Estrutura de Pastas (Monorepo)](#7-estrutura-de-pastas-monorepo)
8. [Padrões de Código](#8-padrões-de-código)
9. [Convenções de API](#9-convenções-de-api)
10. [Regras de Negócio Críticas](#10-regras-de-negócio-críticas)
11. [Sessões de Implementação](#11-sessões-de-implementação)
    - [S0 — Setup do Monorepo e Infraestrutura Local](#s0--setup-do-monorepo-e-infraestrutura-local)
    - [S1 — Núcleo Compartilhado do Backend](#s1--núcleo-compartilhado-do-backend)
    - [S2 — Banco de Dados, Migrations e Seeds](#s2--banco-de-dados-migrations-e-seeds)
    - [S3 — Autenticação (Seção 1 do Backlog)](#s3--autenticação)
    - [S4 — Gestão de Usuários (Seção 2)](#s4--gestão-de-usuários)
    - [S5 — Integração Omie (Cliente HTTP Base)](#s5--integração-omie-cliente-http-base)
    - [S6 — Gestão de Clientes BPO (Seção 3)](#s6--gestão-de-clientes-bpo)
    - [S7 — Detalhe do Cliente + Cache L1 (Seção 4)](#s7--detalhe-do-cliente--cache-l1)
    - [S8 — Formulário de Nova Conciliação + Validações (Seções 5 e 6)](#s8--formulário-de-nova-conciliação--validações)
    - [S9 — Parsing do Arquivo via IA (Seção 7)](#s9--parsing-do-arquivo-via-ia)
    - [S10 — Processamento Automático em Background (Seção 8)](#s10--processamento-automático-em-background)
    - [S11 — Tela de Revisão: Backend + Cache L2 (Seção 9 — backend)](#s11--tela-de-revisão-backend--cache-l2)
    - [S12 — Tela de Revisão: Estrutura + Aba Movimentações (Seção 9 — front 1/2)](#s12--tela-de-revisão-estrutura--aba-movimentações)
    - [S13 — Tela de Revisão: Abas Restantes (Seção 9 — front 2/2)](#s13--tela-de-revisão-abas-restantes)
    - [S14 — Exportação do Relatório Excel (Seção 10)](#s14--exportação-do-relatório-excel)
    - [S15 — Gestão de Tipos de Anomalia (Seção 11)](#s15--gestão-de-tipos-de-anomalia)
    - [S16 — Hardening de Segurança](#s16--hardening-de-segurança)
    - [S17 — Observabilidade e Logs Estruturados](#s17--observabilidade-e-logs-estruturados)
    - [S18 — Testes E2E, Documentação e Deploy](#s18--testes-e2e-documentação-e-deploy)
    - [S19 — Qualificação Inteligente de Lançamentos (Seção 11 ClickUp)](#s19--qualificação-inteligente-de-lançamentos)
12. [Riscos e Mitigações](#12-riscos-e-mitigações)
13. [Pontos em Aberto (precisam de validação)](#13-pontos-em-aberto)

---

## 1. Visão Geral do Sistema

### 1.1 O que é

Sistema **interno** da Hologram Gestão que substitui a conciliação manual linha-a-linha por um fluxo automatizado: o analista faz upload de um extrato bancário ou fatura de cartão, a IA (Claude) extrai as movimentações em formato estruturado, o sistema cruza contra os lançamentos do Omie (ERP do cliente), e gera um relatório Excel auditável com divergências e anomalias.

### 1.2 Personas

| Persona               | Escopo                                                                                                        |
| --------------------- | ------------------------------------------------------------------------------------------------------------- |
| **Admin**             | Vê todos os clientes. Gerencia usuários, reatribui clientes entre gerentes, administra catálogo de anomalias. |
| **Gerente (Manager)** | Vê apenas clientes da sua carteira. Cria clientes (auto-atribuídos). Realiza conciliações e gera relatórios.  |

> Sistema **não é multi-tenant em termos de BPOs** — é de uso exclusivo da Hologram. Multi-cliente aqui significa "múltiplos clientes finais da Hologram", não múltiplas empresas de BPO.

### 1.3 Princípios de Design (invioláveis)

1. **IA só extrai, nunca decide match.** O cruzamento é 100 % determinístico e auditável.
2. **Human-in-the-loop em dois gates:** (a) validação da amostra do parsing antes de salvar, (b) revisão das linhas antes da exportação.
3. **Idempotência por** `(bankAccount, month, fileHash)` — mesmo arquivo não é reprocessado.
4. **Processamento assíncrono** para operações > 2 s (parsing IA, busca Omie, matching, export).
5. **Rastreabilidade** total: toda ação do analista persiste `user_action` e, quando aplicável, gera registro em anomalias.
6. **Nenhum dado identificável do cliente final persiste em claro.** Credenciais Omie e descrições sempre criptografadas.
7. **Arquivos nunca tocam disco.** Processamento em memória, descarte ao final.
8. **Autorização por linha** (`client_assignments`) — um gerente nunca vê cliente de outro.

---

## 2. Stack Tecnológico Definitivo

> Alinhada à documentação oficial (`Docs/documentation/2. Stack Tecnológico-*.md`).

### 2.1 Backend

| Item                         | Escolha                                                                  | Motivo                                                                                                                                                                         |
| ---------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Linguagem**                | Python 3.12+                                                             | Conforme documentação. Ecossistema maduro para dados e IA.                                                                                                                     |
| **Framework web**            | **FastAPI 0.110+**                                                       | Async nativo (crítico para Omie/Claude), Pydantic v2 para DTOs, OpenAPI automática, type hints obrigatórios.                                                                   |
| **ORM**                      | **SQLAlchemy 2.0** (modo async)                                          | Padrão de mercado, suporte async sólido, integração nativa com Alembic.                                                                                                        |
| **Migrations**               | **Alembic**                                                              | Versionamento de schema reproducível.                                                                                                                                          |
| **Driver Postgres**          | **psycopg3** (async)                                                     | Driver oficial moderno, compatível com SQLAlchemy async.                                                                                                                       |
| **Validação/Schemas**        | **Pydantic v2**                                                          | Obrigatório com FastAPI; usado em DTOs, settings e Claude tool schemas.                                                                                                        |
| **HTTP client**              | **httpx** (async)                                                        | Para chamadas ao Omie e Claude — reuso de connection pool.                                                                                                                     |
| **Background jobs**          | **ARQ + Redis**                                                          | Async-first nativo, integra diretamente com código `async def` do FastAPI/httpx/SQLAlchemy, sem workaround de `asyncio.run()` em task síncrona. Mesma infra Redis do cache L2. |
| **JWT**                      | **python-jose[cryptography]**                                            | RFC-compliant, hash assíncrono suportado.                                                                                                                                      |
| **Hash de senha**            | **bcrypt** direto (cost ≥ 12)                                            | bcrypt é o padrão; uso direto porque passlib 1.7.x é incompatível com bcrypt 5.x (trava no import).                                                                            |
| **Criptografia AES-256-GCM** | **cryptography** (pyca)                                                  | Lib oficial, auditada, FIPS-friendly.                                                                                                                                          |
| **Parsing PDF (pré-IA)**     | **pypdf** + fallback **pdfplumber**                                      | Extrair texto para reduzir tokens enviados à Claude.                                                                                                                           |
| **Parsing CSV/XLSX**         | **pandas** ou **openpyxl** direto                                        | Dependendo da complexidade — avaliar em S9.                                                                                                                                    |
| **Geração Excel**            | **openpyxl**                                                             | Formatação fina de cores, estilos, larguras.                                                                                                                                   |
| **Rate limit**               | **slowapi**                                                              | Integra com FastAPI + Redis backend.                                                                                                                                           |
| **Testes**                   | **pytest + pytest-asyncio + httpx AsyncClient + respx + testcontainers** | Unit, integração com DB real e mocks de HTTP externos.                                                                                                                         |
| **Lint/Format**              | **ruff + black + mypy (strict)**                                         | Ruff substitui flake8/isort; black define formatação; mypy obrigatório.                                                                                                        |
| **Config**                   | **pydantic-settings**                                                    | `.env` tipado e validado no startup.                                                                                                                                           |
| **Logger**                   | **structlog**                                                            | Logs estruturados em JSON, com redação obrigatória de segredos.                                                                                                                |

### 2.2 Frontend

| Item                       | Escolha                                               | Motivo                                                                       |
| -------------------------- | ----------------------------------------------------- | ---------------------------------------------------------------------------- |
| **Framework**              | **Next.js 14 (App Router)** + **TypeScript (strict)** | Conforme documentação. App Router para server components onde fizer sentido. |
| **Estilo**                 | **TailwindCSS** + **shadcn/ui**                       | Componentes acessíveis, design system consistente e customizável.            |
| **Estado remoto**          | **TanStack Query v5**                                 | Cache de requests, invalidação granular, polling nativo.                     |
| **Estado local**           | **Zustand**                                           | Estado leve e global quando necessário (ex: filtros da tela de revisão).     |
| **Formulários**            | **react-hook-form + zod**                             | Validação tipada, schemas compartilhados com backend via gerador TS.         |
| **Tabelas**                | **@tanstack/react-table + @tanstack/react-virtual**   | Virtualização para listas grandes (tela de revisão com 2 k+ linhas).         |
| **Datas**                  | **date-fns + date-fns-tz**                            | Lightweight, tree-shakeable, sem moment.                                     |
| **Notifications**          | **sonner** (ou **shadcn/ui toast**)                   | Toasts acessíveis.                                                           |
| **Cryptography (browser)** | **Web Crypto API** nativo                             | Para SHA-256 do arquivo antes de upload.                                     |
| **Testes**                 | **vitest + react-testing-library + playwright**       | Unit, componente, E2E.                                                       |
| **Lint/Format**            | **eslint (next config) + prettier**                   | Padrão.                                                                      |

### 2.3 Infraestrutura

| Item                | Escolha                                                                                               | Motivo                                                                                      |
| ------------------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| **Banco**           | **PostgreSQL 16**                                                                                     | Conforme documentação.                                                                      |
| **Cache + broker**  | **Redis 7**                                                                                           | Cache L2 (lançamentos Omie) + broker Celery. Mesma instância serve aos dois.                |
| **Reverse proxy**   | **Nginx** ou **Traefik**                                                                              | TLS termination, headers de segurança, rate limit de borda.                                 |
| **Container**       | **Docker + Docker Compose** (dev) / **Docker Swarm** ou **AWS ECS** (prod)                            | Isolamento e reprodutibilidade. Swarm/ECS mantém simplicidade — K8s é overengineering aqui. |
| **CI/CD**           | **GitHub Actions**                                                                                    | Lint, type-check, testes, build, deploy.                                                    |
| **Observabilidade** | **Sentry** (erros) + **structlog + Loki/Grafana** (logs)                                              | Stack moderna e barata.                                                                     |
| **Segredos**        | **Variáveis de ambiente** em primeiro momento; **AWS Secrets Manager** ou **HashiCorp Vault** em prod | `OMIE_ENCRYPTION_KEY`, `JWT_SECRET`, `ANTHROPIC_API_KEY` nunca em repositório.              |

> **Decisões formalizadas em 24/04/2026:**
>
> - Framework Python: **FastAPI** (documentação deixou aberto entre FastAPI e Flask; escolhido FastAPI por async nativo + Pydantic integrado + OpenAPI automática).
> - Job runner: **ARQ** (async-first, integra com código `async def` sem wrappers).
> - Package manager Python: **uv**.
> - Package manager frontend: **pnpm**.
> - Estrutura: **monorepo simples** (1 repo GitHub) com `apps/api` + `apps/web` + `packages/shared-types`.

---

## 3. Arquitetura de Alto Nível

```
┌──────────────────┐   HTTPS    ┌─────────────────────┐
│   Next.js SSR    │ ─────────► │   FastAPI (async)   │
│   (Frontend)     │            │   REST JSON         │
└──────────────────┘            └──────────┬──────────┘
                                           │
        ┌──────────────┬───────────────────┼────────────────┬─────────────┐
        ▼              ▼                   ▼                ▼             ▼
 ┌────────────┐ ┌──────────────┐  ┌─────────────────┐  ┌──────────┐ ┌────────────┐
 │ PostgreSQL │ │ Redis        │  │ Omie API        │  │ Claude   │ │ Sentry/    │
 │ (SQLAlch.) │ │ (Celery+L2)  │  │ (SOAP-like JSON)│  │ API      │ │ Grafana    │
 └────────────┘ └──────┬───────┘  └─────────────────┘  └──────────┘ └────────────┘
                       │
               ┌───────┴────────┐
               ▼                ▼
         ┌──────────┐     ┌──────────┐
         │ Parser   │     │ Matcher  │   ◄── Celery workers
         │ Worker   │     │ Worker   │
         └──────────┘     └──────────┘
```

### 3.1 Fluxo completo de uma conciliação

1. **Upload (browser):** frontend calcula `SHA-256` do arquivo (Web Crypto), envia **apenas o hash** ao backend para checar duplicata (endpoint barato).
2. **Parsing (síncrono, 60 s max):** se não é duplicata, o arquivo é enviado ao backend em `multipart/form-data`. Backend converte para base64 em memória e envia à Claude API com tool use forçando o schema `extract_movements`. Arquivo **nunca toca disco**.
3. **Preview:** frontend recebe prévia (metadados + primeiras 5 linhas) e exibe para confirmação humana. Nada foi salvo ainda.
4. **Confirmação:** usuário confirma → backend cria `reconciliation_sessions (status='processing')` + `reconciliation_file_entries[]` (descrições criptografadas). Enfileira job Celery. Retorna `session_id`.
5. **Background worker:**
   a. Busca `ListarExtrato` (período expandido pela tolerância).
   b. Busca `ListarContasPagar` e `ListarContasReceber` (status ATRASADO e PREVISTO).
   c. Executa algoritmo determinístico de matching.
   d. Persiste classificação final (`conciliado` / `sem_omie` / Omie sem arquivo).
   e. Cria anomalias estruturais automáticas.
   f. Atualiza `status='reviewing'`.
6. **Polling (frontend):** `GET /reconciliations/:id/status` a cada 3 s. Quando `status='reviewing'`, redireciona para tela de revisão.
7. **Revisão (4 abas):** analista age sobre linhas (Confirmar / Trocar / Anotar / Ignorar / Flag / Registrar anomalia / Resolver anomalia). Cada ação é um `PATCH` pontual.
8. **Export:** botão "Exportar Relatório" → job Celery gera Excel com 5 abas → download direto (Content-Disposition attachment).

### 3.2 Fronteira de segurança cliente × servidor

| Roda no **browser**                | Roda no **servidor**                     | **Nunca** no browser                       |
| ---------------------------------- | ---------------------------------------- | ------------------------------------------ |
| SHA-256 do arquivo (Web Crypto)    | Criptografia/descriptografia AES-256-GCM | Credenciais Omie (mesmo descriptografadas) |
| Validação de extensão/tamanho (UX) | Todas as chamadas ao Omie                | `OMIE_ENCRYPTION_KEY`                      |
| Exibição e interação               | Todas as chamadas à Claude API           | `ANTHROPIC_API_KEY`                        |
| Polling de status                  | Validações de autorização                | Hash da senha de qualquer usuário          |

---

## 4. Modelo de Dados

> **Fonte da verdade:** `Docs/documentation/0. Schema do Banco de Dados e Cache-*.md`. Este plano não redefine o schema — apenas referencia e adiciona notas de implementação.

### 4.1 Tabelas (resumo)

```
users                         — usuários internos (admin, manager)
clients                       — clientes BPO (credenciais Omie criptografadas)
client_assignments            — N:1 cliente → gerente (UNIQUE client_id)
omie_accounts_cache           — Cache L1 contas correntes (TTL 24 h)
reconciliation_sessions       — 1 upload = 1 sessão
reconciliation_file_entries   — linhas do arquivo (descrição criptografada)
reconciliation_omie_entries   — lançamentos Omie sem correspondente
anomaly_types                 — catálogo seed + admin-managed
reconciliation_anomalies      — anomalias detectadas por sessão
```

### 4.2 Campos criptografados (AES-256-GCM)

| Tabela                        | Campo(s)                                                                                    |
| ----------------------------- | ------------------------------------------------------------------------------------------- |
| `clients`                     | `omie_app_key_encrypted`, `omie_app_secret_encrypted` (+ `encryption_iv`, `encryption_tag`) |
| `reconciliation_file_entries` | `description_encrypted`, `user_note_encrypted`                                              |
| `reconciliation_omie_entries` | `user_note_encrypted`                                                                       |
| `reconciliation_anomalies`    | `context_encrypted`, `resolution_note_encrypted`                                            |

> **Regra:** cada operação de criptografia gera **IV novo** (12 bytes). IV e tag GCM são armazenados em colunas separadas (formato hex ou base64 — padronizar em S1).

### 4.3 Notas de implementação SQLAlchemy

- Usar **UUID v4** em todas as PKs (`uuid.uuid4`, coluna `UUID` do Postgres).
- `TIMESTAMPTZ` em todas as colunas de data/hora; Python `datetime` com timezone aware.
- `DECIMAL(14,2)` vira `Numeric(14, 2)` em SQLAlchemy → mapear para `decimal.Decimal` em Python — **nunca usar float** para valores monetários.
- Índices obrigatórios conforme documentação — criá-los na migration inicial, não em migrations subsequentes.
- Soft delete via `active BOOLEAN` — nunca `DELETE` em `users`, `clients`, `anomaly_types`. Em sessões canceladas, considerar status explícito.

---

## 5. Segurança — Regras Invioláveis

> **Fonte:** `Docs/documentation/4. Segurança e Criptografia-*.md`.

### 5.1 Lista de invariants

1. **Chave de criptografia `OMIE_ENCRYPTION_KEY` SÓ existe como variável de ambiente.** Nunca em banco, nunca em log, nunca em resposta de API, nunca em código. Ao rotacionar → re-criptografar todos os registros afetados em uma operação atômica.
2. **Credenciais Omie:** apenas descriptografadas em **memória de processo**, durante a chamada. Garbage-collect imediatamente. Logar sempre `[REDACTED]`.
3. **Senhas de usuários:** bcrypt com **cost ≥ 12**. Nunca retornar o hash em endpoint algum.
4. **Tokens JWT:**
   - Access token: 1 h de expiração. Claims mínimos: `sub`, `role`, `exp`, `iat`, `jti`.
   - Refresh token: 7 dias. Armazenamento em **cookie HttpOnly + Secure + SameSite=Lax**. Nunca em `localStorage`.
   - Invalidação: middleware valida `users.active = true` a cada request — usuário desativado perde acesso imediato mesmo com token vivo.
5. **Autorização por linha:** todo endpoint que acessa `clients`, `reconciliation_*` OU dados do Omie deve verificar `client_assignments` para managers. Admin bypassa. **Default deny**: se em dúvida, 403.
6. **Input validation:** 100 % via Pydantic. `Any` é banido em DTOs. Campos numéricos usam tipos restritos (`conint`, `condecimal`).
7. **SQL Injection:** proibido SQL cru. SQLAlchemy sempre parametrizado. Se for preciso SQL manual (raríssimo), usar `text()` com `bindparams`.
8. **Uploads:**
   - Extensão verificada **no servidor** (cliente apenas UX).
   - **Magic bytes** validados (PDF: `%PDF-`; XLSX: `PK\x03\x04`; etc.).
   - Tamanho máximo **20 MB** enforçado no servidor (NGINX + FastAPI `Request.body` streamed).
   - Arquivo em memória, nunca em disco.
9. **XSS:** toda descrição vinda do arquivo é potencial vetor. Frontend nunca injeta em `dangerouslyInnerHTML`. Sanitizar com `DOMPurify` se algum dia for necessário renderizar HTML.
10. **CSRF:** como frontend e backend estão em origens diferentes (SPA + API), usar **double-submit cookie** ou header `X-CSRF-Token` obrigatório em POST/PATCH/DELETE. Alternativa: `SameSite=Strict` nos cookies de auth + validação de `Origin`/`Referer`.
11. **Rate limiting:**
    - Login: **5 tentativas / 5 min / IP+email**.
    - Endpoints autenticados gerais: **120 req/min / user**.
    - Endpoints pesados (parsing, export): **10 req/min / user**.
12. **Headers de segurança:** CSP restritivo, `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`.
13. **Logs:**
    - **Nunca** logar: senhas, credenciais Omie, JWTs, conteúdo de arquivos, CPFs/CNPJs, emails completos (mascarar `j***@dominio.com`).
    - Incluir `correlation_id` por request.
    - Logs em JSON estruturado via structlog.
14. **Backup:** `pg_dump` diário criptografado (GPG) → S3/armazenamento off-site com retenção mínima de 30 dias.
15. **Dependências:** `pip-audit` e `npm audit` no CI — falha se CVE crítico sem mitigação.

### 5.2 Modelo de ameaças (STRIDE resumido)

| Ameaça                                 | Mitigação                                                                         |
| -------------------------------------- | --------------------------------------------------------------------------------- |
| Spoofing (falsificar usuário)          | JWT assinado + middleware verifica `active`                                       |
| Tampering (alterar dado criptografado) | AES-GCM detecta via tag; descriptografia falha                                    |
| Repudiation (negar ação)               | Audit log em toda mutation crítica                                                |
| Information disclosure                 | Criptografia em repouso + TLS em trânsito + redação de logs                       |
| Denial of Service                      | Rate limit + timeout rígido Omie (15 s) / Claude (60 s) + max file 20 MB          |
| Elevation of Privilege                 | RBAC verificado em **toda** rota; `client_assignments` em toda leitura de cliente |

---

## 6. Análise de Escalabilidade

### 6.1 Estimativa de carga (hipótese de trabalho — validar com stakeholder)

| Métrica                                     | Valor esperado                 |
| ------------------------------------------- | ------------------------------ |
| Analistas ativos simultâneos                | 5 – 30                         |
| Clientes BPO cadastrados                    | 100 – 1 000                    |
| Conciliações por mês                        | 500 – 5 000                    |
| Pico: concorrência de jobs de processamento | 10 – 30                        |
| Tamanho típico de arquivo                   | 100 – 2 000 linhas / 1 – 10 MB |
| Latência média Omie                         | 300 – 800 ms/chamada           |

### 6.2 Gargalos conhecidos

1. **Latência Omie:** cada chamada 300–800 ms; uma sessão pode gerar 20+ chamadas (paginação). Mitigação: **httpx com connection pooling**, chamadas paralelas (`asyncio.gather`) quando possível, cache L1 (24 h) e L2 (2 h).
2. **Claude API:** limite de 60 s no parsing; custo por token. Mitigação: **prompt caching** (reduz ~90 % do custo em volume), pré-processamento PDF → texto (evita tokens de imagem), `claude-sonnet-4-5` como modelo padrão, `opus` apenas para arquivos complexos.
3. **Matching O(n × m):** 2 000 linhas × 2 000 lançamentos = 4 M comparações. Mitigação: **indexar Omie por valor** (hash map `amount → [entries]`) → matching fica O(n).
4. **Excel em memória:** até ~10 k linhas é trivial com openpyxl. Acima disso, avaliar `xlsxwriter` em modo constant_memory.

### 6.3 Arquitetura escalável por padrão

- **API stateless** → escala horizontal trivial (2–5 instâncias atrás de um LB bastam para esse volume).
- **Workers Celery** escalam independentemente → aumentar concorrência no pico do mês.
- **Cache L2 abstraído** (interface `AsyncCache.get/set/delete`) → começar em Map in-memory, migrar para Redis sem tocar nos callers.
- **DB único com réplica de leitura opcional** → PostgreSQL managed (RDS/Supabase) suporta esta carga por anos.
- **Evitar premature optimization:** nada de sharding, CQRS, event sourcing. Monolito modular + workers é suficiente.

### 6.4 Veredicto

**Sim, precisa ser escalável, mas com moderação.** A arquitetura recomendada atende 10 × a carga prevista sem reestruturação. Os pontos críticos para **não tornar escalabilidade difícil depois** são:

- Código async puro (não bloquear event loop).
- Cache com interface abstrata.
- Jobs via broker desde o MVP (não `FastAPI.BackgroundTasks`, que morrem com o processo).
- Configuração 100 % via env vars (12-factor).

---

## 7. Estrutura de Pastas (Monorepo)

```
auditoria-lancamentos/
├── apps/
│   ├── api/                                # Backend FastAPI
│   │   ├── app/
│   │   │   ├── core/                       # Config, security, crypto, errors, logging
│   │   │   │   ├── config.py               # pydantic-settings
│   │   │   │   ├── security.py             # JWT, bcrypt, deps
│   │   │   │   ├── crypto.py               # AES-256-GCM
│   │   │   │   ├── exceptions.py           # Exceções custom
│   │   │   │   ├── logging.py              # structlog config
│   │   │   │   └── dependencies.py         # FastAPI Depends (DB, current_user, role)
│   │   │   ├── db/
│   │   │   │   ├── base.py                 # Declarative base
│   │   │   │   ├── session.py              # AsyncSession factory
│   │   │   │   └── models/                 # SQLAlchemy models (um arquivo por tabela)
│   │   │   ├── schemas/                    # Pydantic DTOs (request + response)
│   │   │   ├── modules/
│   │   │   │   ├── auth/                   # routes.py, service.py, schemas.py
│   │   │   │   ├── users/
│   │   │   │   ├── clients/
│   │   │   │   ├── reconciliations/
│   │   │   │   ├── anomalies/
│   │   │   │   └── reports/
│   │   │   ├── integrations/
│   │   │   │   ├── omie/                   # client.py, exceptions.py, schemas.py, cache.py
│   │   │   │   └── anthropic/              # client.py, tools.py, prompts.py
│   │   │   ├── workers/
│   │   │   │   ├── celery_app.py
│   │   │   │   ├── parser_task.py
│   │   │   │   ├── matcher_task.py
│   │   │   │   └── exporter_task.py
│   │   │   ├── cache/                      # Abstração L2
│   │   │   │   ├── base.py                 # AsyncCache interface
│   │   │   │   ├── memory.py               # InMemoryCache (MVP)
│   │   │   │   └── redis.py                # RedisCache (prod)
│   │   │   ├── utils/                      # Helpers puros (datas, decimal, magic bytes)
│   │   │   └── main.py                     # FastAPI app factory
│   │   ├── alembic/
│   │   │   ├── env.py
│   │   │   └── versions/
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   ├── integration/
│   │   │   └── conftest.py                 # testcontainers, fixtures
│   │   ├── pyproject.toml
│   │   ├── ruff.toml
│   │   ├── mypy.ini
│   │   └── .env.example
│   └── web/                                # Frontend Next.js
│       ├── src/
│       │   ├── app/
│       │   │   ├── (auth)/login/
│       │   │   ├── (app)/
│       │   │   │   ├── layout.tsx          # middleware de auth + layout com sidebar
│       │   │   │   ├── clientes/
│       │   │   │   │   ├── page.tsx        # lista
│       │   │   │   │   └── [clientId]/
│       │   │   │   │       ├── page.tsx    # detalhe
│       │   │   │   │       └── conciliacao/
│       │   │   │   │           ├── nova/page.tsx
│       │   │   │   │           └── [sessionId]/
│       │   │   │   │               ├── processando/page.tsx
│       │   │   │   │               └── page.tsx   # revisão
│       │   │   │   └── configuracoes/
│       │   │   │       ├── usuarios/page.tsx
│       │   │   │       └── anomalias/page.tsx
│       │   │   ├── api/auth/               # handlers Next (proxy para API Python se necessário)
│       │   │   └── layout.tsx              # root
│       │   ├── components/
│       │   │   ├── ui/                     # shadcn/ui generated
│       │   │   └── features/               # componentes de domínio (ReviewTable, etc.)
│       │   ├── lib/
│       │   │   ├── api/                    # cliente HTTP (fetch wrapper + interceptors)
│       │   │   ├── auth/                   # helpers de sessão
│       │   │   ├── crypto/                 # SHA-256 Web Crypto
│       │   │   ├── validation/             # zod schemas
│       │   │   └── formatters/             # BRL, dates
│       │   ├── hooks/                      # useAuth, usePaginatedQuery, etc.
│       │   └── stores/                     # Zustand slices
│       ├── tests/
│       │   ├── unit/                       # vitest
│       │   └── e2e/                        # playwright
│       ├── package.json
│       ├── tsconfig.json
│       ├── tailwind.config.ts
│       └── .env.example
├── packages/
│   └── shared-types/                       # opcional — tipos compartilhados gerados do OpenAPI
├── docker/
│   ├── docker-compose.yml                  # postgres + redis + api + worker + web
│   ├── docker-compose.prod.yml
│   ├── Dockerfile.api
│   ├── Dockerfile.worker
│   └── Dockerfile.web
├── .github/
│   └── workflows/
│       ├── ci.yml                          # lint + type + test
│       └── deploy.yml
├── scripts/
│   ├── rotate-encryption-key.py
│   └── seed-dev.py
├── CLAUDE.md                               # contexto para agentes
├── Docs/
│   ├── PLANO_IMPLEMENTACAO.md              # este arquivo
│   ├── documentation/                      # fonte da verdade funcional
│   └── flow/
└── README.md
```

---

## 8. Padrões de Código

### 8.1 Backend (Python)

- **Type hints obrigatórios em 100 % do código.** Funções sem hints não passam no CI.
- **Mypy em modo strict** (`strict = True` no `mypy.ini`).
- **Ruff** com regras ativadas: `E, F, I, N, W, UP, B, C4, SIM, RUF`.
- **Black** com `line-length = 100`.
- **Docstrings** nos módulos públicos e em qualquer função cuja intenção não seja óbvia pelo nome. Estilo Google.
- **Funções puras** para lógica de domínio (matcher, crypto, formatters). Testáveis sem DB.
- **Dependency Injection** via `Depends` do FastAPI — proibido importar `session` global.
- **Padrão de módulo:**
  ```
  modules/<domain>/
    routes.py      # APIRouter, apenas delega
    service.py     # lógica de negócio, recebe repository
    repository.py  # acesso a DB
    schemas.py     # DTOs request + response (Pydantic)
    models.py      # (opcional) se quiser isolar SQLAlchemy por módulo
  ```
- **Erros:** lançar exceção custom (`DuplicateFileError`, `OmieAuthError`) que um exception handler global converte para o formato de resposta padrão (§9).
- **`async def` para tudo** que toca I/O. `def` síncrono apenas em funções puras (matcher, crypto).
- **`Decimal`** para valores monetários. **Nunca** `float`.
- **`UUID`** para identificadores. **Nunca** expor IDs sequenciais.

### 8.2 Frontend (TypeScript/React)

- **TypeScript strict:** `strict: true`, `noUncheckedIndexedAccess: true`.
- **Componentes:** function components com arrow functions. Props tipadas via interface, não type (preferência).
- **Server components vs client components:** preferir server components; marcar `"use client"` apenas quando necessário (interatividade, hooks).
- **Data fetching:**
  - Leituras autenticadas: `useQuery` (TanStack) em client components OU fetch em server components.
  - Mutations: `useMutation` + invalidação de cache por key.
- **Formulários:** sempre `react-hook-form` + zod schema. `zodResolver`.
- **Componentes grandes:** extrair em pastas `components/features/<feature>/`.
- **Estado:** Zustand para estado verdadeiramente global (ex: user atual após login). Props drilling é preferível a store global para estados locais.
- **Acessibilidade:** shadcn/ui já entrega a11y. Revisar `aria-*` em componentes customizados. Suporte a teclado obrigatório.
- **Testes:**
  - Vitest + Testing Library para componentes críticos (formulário, ReviewTable).
  - Playwright E2E cobrindo o fluxo golden: login → novo cliente → nova conciliação → revisão → export.

### 8.3 Convenções gerais

- **Commits:** Conventional Commits (`feat:`, `fix:`, `chore:`, `refactor:`, `test:`, `docs:`). Enforcement via commitlint + husky.
- **Branches:** `main` protegida. PRs obrigatórios com ≥ 1 review. Branch: `feat/S3-endpoint-login`, `fix/S11-cache-miss`, etc.
- **Variáveis:** `snake_case` no Python, `camelCase` no TS, `SCREAMING_SNAKE` para env vars.
- **Idioma:** código em inglês; comentários/docstrings em português são aceitos onde clarificam domínio de negócio; mensagens ao usuário final **sempre** em português.
- **Sem comentários do tipo "// TODO: fix later"** — ou abrir issue ou resolver.
- **Reuso:** qualquer trecho copiado mais de 1 vez vira função compartilhada. Módulos transversais ficam em `app/core/` (back) ou `src/lib/` (front).

---

## 9. Convenções de API

### 9.1 Padrão de resposta (alinhado à §18 da documentação)

**Sucesso:**

```json
{ "data": { ... } }
```

ou, em listas paginadas:

```json
{
  "data": [ ... ],
  "pagination": {
    "page": 1,
    "pageSize": 20,
    "total": 134,
    "totalPages": 7
  }
}
```

**Erro:**

```json
{
  "error": {
    "code": "DUPLICATE_FILE",
    "message": "File with hash abc... already processed",
    "userMessage": "Este arquivo já foi processado para esta conta e mês."
  }
}
```

### 9.2 Códigos canônicos

| Código             | HTTP | Situação                                      |
| ------------------ | ---- | --------------------------------------------- |
| `VALIDATION_ERROR` | 400  | Dados inválidos (Pydantic)                    |
| `UNAUTHORIZED`     | 401  | Token ausente/inválido                        |
| `TOKEN_EXPIRED`    | 401  | Access expirou; frontend deve tentar refresh  |
| `FORBIDDEN`        | 403  | Role insuficiente ou não atribuído ao cliente |
| `NOT_FOUND`        | 404  | Recurso inexistente                           |
| `DUPLICATE_FILE`   | 409  | Idempotência violada                          |
| `RATE_LIMITED`     | 429  | Limite de requests excedido                   |
| `OMIE_AUTH_ERROR`  | 502  | Credenciais Omie recusadas                    |
| `OMIE_TIMEOUT`     | 504  | Omie não respondeu em 15 s                    |
| `PARSE_ERROR`      | 422  | Claude API não extraiu movimentações          |
| `INTERNAL_ERROR`   | 500  | Erro inesperado (log + Sentry)                |

### 9.3 Versionamento de API

- Prefixo `/api/v1/...`.
- Breaking changes → `/api/v2/...` com coexistência por 1 ciclo.

### 9.4 Paginação

- Padrão: `?page=1&pageSize=20`.
- Limite máximo `pageSize = 100`.
- Listagens de revisão (linhas do arquivo): suportar também cursor-based no futuro se necessário.

### 9.5 Idempotência

- Criação de sessão de conciliação é idempotente via `(bankAccountId, fileHash, month)`. Se o mesmo arquivo é postado 2 ×, retorna 409 `DUPLICATE_FILE` com `existing_session_id` no payload.

---

## 10. Regras de Negócio Críticas

> Referência cruzada com documentação seções 6, 13, 14.

| Regra                       | Detalhe                                                                                                                                       |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- | ----------------------------------------------------------------------------------- | ----------- | ------------------------------------------------- |
| **Tolerância de valor**     | `                                                                                                                                             | a − b                                             | ≤ 0.01 BRL` (absorve arredondamento de centavos).                                   |
| **Tolerância de data**      | Parametrizável por conciliação; padrão 3 dias; opções 1/2/3/5/7.                                                                              |
| **Período expandido Omie**  | Busca `[period_start − tolerância, period_end + tolerância]` para capturar lançamentos de borda.                                              |
| **Desempate de matches**    | Menor `                                                                                                                                       | days_diff                                         | `; se empate, menor `                                                               | amount_diff | `; se ainda empate, primeiro Omie por `date asc`. |
| **Dupla alocação**          | Um `OmieEntry` só pode matchar 1 `Movement`. Manter set de IDs consumidos.                                                                    |
| **Idempotência**            | `UNIQUE(client_id, omie_conta_id, reference_month, file_hash)`.                                                                               |
| **Saldo anterior**          | Vem do próprio arquivo parseado pela Claude (`opening_balance`). Fallback: buscar do Omie no 1º dia do mês.                                   |
| **Anomalias estruturais**   | Criadas automaticamente com `detected_by = 'ai'` para `missing_in_omie` (cada `sem_omie`) e `missing_in_file` (cada Omie Atrasado sem match). |
| **Normalização Omie**       | `cNatureza='D'` → `valor = nValorLanc × -1`; `cNatureza='C'` → manter.                                                                        |
| **Status Omie considerado** | Somente `Conciliado`, `Atrasado` ou `Previsto` entram no matching. Cancelados são ignorados.                                                  |
| **Validação de saldos**     | Se `                                                                                                                                          | balance_end_file − sum(movements) − balance_start | > 0.01` → flag "saldo do arquivo não bate com as movimentações" (validação pós-IA). |

---

## 11. Sessões de Implementação

> Cada sessão é autocontida e cobre um conjunto coeso de tarefas. **Uma sessão = uma ou mais conversas de trabalho com o Claude.**
>
> **Convenção:**
>
> - ▸ **Tarefas do backlog:** IDs como `[BACK 1.1]` / `[FRONT 1.3]` vêm do PDF `Docs/List _ ... TAREFAS.pdf`.
> - ▸ **Doc:** referências a arquivos em `Docs/documentation/`.
> - ▸ **DoD (Definition of Done)** é o checklist obrigatório antes de marcar a sessão como concluída.

### Mapa de dependências entre sessões

```
S0 ─► S1 ─► S2 ─┬─► S3 ─► S4 ─► S5 ─► S6 ─► S7 ─► S8 ─► S9 ─► S10 ─► S11 ─► S12 ─► S13 ─► S14 ─► S15
                │                                                                                  │
                └────────────────────────────── S16, S17 e S18 podem começar em paralelo ────────┘
```

---

### S0 — Setup do Monorepo e Infraestrutura Local

**Objetivo:** preparar o esqueleto do projeto em que todas as sessões seguintes vão construir. Nada funcional ainda — apenas tooling, lint, containers, pipeline básica.

**Tarefas do backlog cobertas:** — (fundação não listada)
**Pré-requisitos:** nenhum.
**Duração estimada:** 1 sessão longa (4 – 6 h).

**Entregáveis:**

1. Estrutura de pastas exatamente como §7.
2. `docker-compose.yml` com serviços `postgres`, `redis`, `api`, `worker`, `web` (todos buildáveis mas ainda triviais).
3. `pyproject.toml` do backend com deps mínimas (FastAPI, SQLAlchemy, Alembic, Pydantic, pytest, ruff, black, mypy).
4. `package.json` do frontend com Next.js 14, TS strict, Tailwind, shadcn/ui inicializado.
5. `.env.example` em ambos os apps com **todas** as variáveis previstas (comentadas):
   - `DATABASE_URL`, `REDIS_URL`
   - `OMIE_ENCRYPTION_KEY` (instrução: gerar com `openssl rand -hex 32`)
   - `JWT_SECRET`
   - `ANTHROPIC_API_KEY`
   - `FRONTEND_URL`, `BACKEND_URL`
   - `LOG_LEVEL`, `ENVIRONMENT`
6. Husky + commitlint + lint-staged configurados.
7. GitHub Actions mínimo: `lint`, `type-check`, `test` (hello-world) rodando em push e PR.
8. `README.md` raiz com instruções de setup em 3 comandos.

**Decisões formalizadas (24/04/2026):**

- [x] Framework Python: **FastAPI**
- [x] Job runner: **ARQ**
- [x] Package manager Python: **uv**
- [x] Package manager frontend: **pnpm**
- [x] Estrutura: **monorepo simples**

**DoD:**

- [ ] `docker compose up` sobe todos os serviços sem erro.
- [ ] `curl http://localhost:8000/health` retorna `200 OK`.
- [ ] `curl http://localhost:3000/` serve página Next padrão.
- [ ] CI verde em PR.
- [ ] README.md permite que dev novo rode o projeto em < 15 min.

---

### S1 — Núcleo Compartilhado do Backend

**Objetivo:** módulos que o resto do sistema depende — criptografia, JWT, logging estruturado, config, exception handling.

**Tarefas do backlog cobertas:** — (infraestrutura compartilhada).
**Pré-requisitos:** S0.
**Duração estimada:** 1 sessão (3 – 5 h).

**Entregáveis:**

1. **`app/core/config.py`** — `Settings(BaseSettings)` com todas as env vars tipadas e validadas. Falha rápido no startup se alguma obrigatória faltar.
2. **`app/core/crypto.py`** — funções puras:
   ```python
   def encrypt(plaintext: str, key: bytes) -> EncryptedPayload  # {ciphertext, iv, tag}
   def decrypt(payload: EncryptedPayload, key: bytes) -> str
   ```
   Testes exaustivos: round-trip, IV único por chamada, tag detecta tampering, key errada falha, plaintext vazio, unicode.
3. **`app/core/security.py`** — JWT encode/decode, bcrypt hash/verify, geração de tokens access+refresh, claim `jti` para revogação futura.
4. **`app/core/logging.py`** — structlog em JSON, com processor de redação que mascara keys sensíveis (`password`, `token`, `app_key`, `app_secret`, `authorization`, `cookie`). `correlation_id` via middleware.
5. **`app/core/exceptions.py`** — hierarquia `AppError` → `DuplicateFileError`, `OmieAuthError`, `OmieTimeoutError`, `ParseError`, `ForbiddenError`, etc. Cada uma carrega `code`, `user_message`.
6. **`app/core/dependencies.py`** — `Depends` comuns: `get_db`, `get_current_user`, `require_admin`, `require_client_access(client_id)`.
7. **Global exception handler** em `main.py` convertendo `AppError` no formato §9.
8. **`app/utils/magic_bytes.py`** — detecção de tipo real do arquivo (PDF, XLSX, XLS, CSV).

**DoD:**

- [ ] 100 % dos módulos com type hints e passando mypy strict.
- [ ] Testes unitários cobrindo crypto (round-trip, tampering, IV único).
- [ ] Logs locais mostram JSON estruturado sem segredos.
- [ ] Erro customizado testado retorna body no formato padrão §9.

---

### S2 — Banco de Dados, Migrations e Seeds

**Objetivo:** traduzir o schema documentado em código, com migrations versionadas e seeds reproduzíveis.

**Tarefas do backlog cobertas:** — (fundação back).
**Pré-requisitos:** S0, S1.
**Duração estimada:** 1 sessão (4 – 6 h).

**Entregáveis:**

1. **Modelos SQLAlchemy** em `app/db/models/*.py`:
   - `user.py`, `client.py`, `client_assignment.py`, `omie_account_cache.py`
   - `reconciliation_session.py`, `reconciliation_file_entry.py`, `reconciliation_omie_entry.py`
   - `anomaly_type.py`, `reconciliation_anomaly.py`
     Cada modelo com tipos exatos conforme doc seção 0 (UUID, TIMESTAMPTZ, DECIMAL(14,2), etc.).
2. **Alembic** configurado (`alembic.ini`, `env.py` com URL do Settings).
3. **Migration inicial** criando todas as tabelas + todos os índices de uma vez.
4. **Seeds** em `scripts/seed-dev.py`:
   - 1 admin (senha configurada via env var em dev).
   - Catálogo completo de `anomaly_types` (8 tipos iniciais conforme doc §0).
5. **Repository base** `app/db/repository.py` com CRUD genérico tipado (opcional — pode ser por módulo).
6. **Fixtures de teste** (`tests/conftest.py`) usando `testcontainers` para subir Postgres real.

**DoD:**

- [ ] `alembic upgrade head` em banco limpo cria todas as tabelas.
- [ ] `scripts/seed-dev.py` popula sem erro.
- [ ] Testes de integração já rodam (esqueleto).
- [ ] Relacionamentos (`relationship`) com `lazy='selectin'` ou `raiseload` onde apropriado (prevenir N+1).

---

### S3 — Autenticação

**Objetivo:** login funcional com JWT, middleware de proteção e tela de login pronta.

**Tarefas do backlog cobertas:**

- `[BACK 1.1]` Endpoint de Login
- `[BACK 1.2]` Middleware de Autenticação e Renovação
- `[FRONT 1.3]` Tela de Login

**Pré-requisitos:** S0, S1, S2.
**Duração estimada:** 1 sessão (5 h).
**Referência doc:** `7. Autenticação e Controle de Acesso`.

**Entregáveis:**

1. **Backend:**
   - `modules/auth/routes.py`: `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`, `POST /api/v1/auth/logout`.
   - `modules/auth/service.py`: lógica de verificação de senha, emissão de tokens, validação de `active`.
   - **Response setta cookies HttpOnly + Secure + SameSite=Lax** (`access_token`, `refresh_token`).
   - Middleware / dependency que extrai `user` de cookie, valida JWT, valida `active` no DB a cada request, injeta `request.state.user`.
   - Rate limit específico no login: 5 tentativas / 5 min / IP+email, via slowapi.
   - Response genérico em erro: "E-mail ou senha incorretos" (não revela qual).

2. **Frontend:**
   - `app/(auth)/login/page.tsx` conforme doc §7.1 (layout, campos, estados de erro, toggle de visibilidade).
   - `lib/api/client.ts`: wrapper fetch com interceptor que detecta `401 TOKEN_EXPIRED` → chama `/refresh` → repete request; se refresh falha → redireciona `/login`.
   - Zustand store `stores/auth.ts` com user atual.
   - Proteção de rotas `app/(app)/layout.tsx` — verifica cookie de auth em middleware Next.

**DoD:**

- [ ] Login com credenciais válidas seta cookies e redireciona para `/clientes`.
- [ ] Login inválido mostra erro genérico, não exposição de detalhes.
- [ ] Rate limit testado (6ª tentativa retorna 429).
- [ ] Access expirado é renovado silenciosamente via refresh.
- [ ] Usuário com `active=false` perde acesso na próxima request.
- [ ] Logout limpa cookies e redireciona.
- [ ] Testes: unit (service), integration (endpoints), E2E (fluxo feliz).

---

### S4 — Gestão de Usuários

**Objetivo:** CRUD completo de usuários, acessível apenas ao Admin.

**Tarefas do backlog cobertas:**

- `[BACK 2.1]` CRUD de Usuários (6 subtarefas)
- `[FRONT 2.2]` Tela de Gestão de Usuários (5 subtarefas)

**Pré-requisitos:** S3.
**Duração estimada:** 1 sessão (5 h).
**Referência doc:** `8. Gestão de Usuários`.

**Entregáveis:**

1. **Backend `modules/users/`:**
   - `GET /api/v1/users?search=&page=&pageSize=` (admin-only)
   - `POST /api/v1/users` (criar; valida unicidade email; valida senha ≥ 8; hash bcrypt)
   - `PATCH /api/v1/users/:id` (editar nome, email, role; admin não pode rebaixar a si mesmo)
   - `POST /api/v1/users/:id/deactivate` (toggle; admin não pode se desativar)
   - `POST /api/v1/users/:id/activate`
   - Todos com dependency `require_admin`.

2. **Frontend `/configuracoes/usuarios`:**
   - Tabela paginada com busca (debounce 300 ms).
   - Modal "Novo Usuário" com validação zod.
   - Modal "Editar Usuário" (sem senha).
   - Modal confirmação para desativar.
   - Link no menu lateral visível apenas para admin.

**DoD:**

- [ ] Manager recebe 403 ao tentar acessar rota ou endpoint.
- [ ] Criação com email duplicado retorna erro inline.
- [ ] Admin desativando a si mesmo é bloqueado (front + back).
- [ ] Lista atualiza após criação sem reload.
- [ ] Testes cobrindo RBAC negativo + fluxo feliz.

---

### S5 — Integração Omie (Cliente HTTP Base)

**Objetivo:** camada isolada e testável para falar com o Omie, com retry, timeout, tratamento de `faultstring`, logs redatados.

**Tarefas do backlog cobertas:** — (base reusada por S6-S10).
**Pré-requisitos:** S1, S2.
**Duração estimada:** 1 sessão (5 – 6 h).
**Referência doc:** `6. Integração com API do Omie`.

**Entregáveis:**

1. **`integrations/omie/client.py`** — classe `OmieClient` assíncrona com:
   - Método genérico `call(module: str, endpoint: str, method: str, param: dict)` que monta o envelope `{call, app_key, app_secret, param}`.
   - **Retry com exponential backoff** (tenacity) para 5xx e timeouts; **não** retry em faultstring (erro lógico).
   - Timeout 15 s.
   - Log de cada chamada com `correlation_id`, endpoint, duração, status — nunca com credenciais.
   - Handler de `faultstring` → lança `OmieFaultError(fault_code, fault_string)`.

2. **Métodos tipados** para cada endpoint utilizado:
   - `listar_clientes(pagina=1, registros=1) -> dict` (para testar conexão)
   - `listar_contas_correntes(pagina, registros) -> list[ContaCorrente]`
   - `listar_extrato(n_cod_cc, data_inicial, data_final) -> list[LancamentoExtrato]`
   - `listar_contas_pagar(pagina, registros, data_de, data_ate, conta_corrente, status) -> PaginatedResult`
   - `listar_contas_receber(...)`
     Com paginação automática (método auxiliar `paginate()`).

3. **DTOs em `integrations/omie/schemas.py`** (Pydantic):
   - `ContaCorrente`, `LancamentoExtrato`, `TituloAPagar`, `TituloAReceber`.
   - Normalização dentro do DTO (ex: `cNatureza='D'` → valor negativo).

4. **Testes com `respx`** simulando respostas Omie (sucesso, faultstring, timeout, 5xx) — snapshots de payloads reais (quando conseguir credenciais sandbox).

**DoD:**

- [ ] `listar_contas_correntes` retorna DTOs tipados para payload real de sandbox.
- [ ] Erro `faultstring` vira exceção custom.
- [ ] Timeout de 15 s enforçado.
- [ ] Paginação automática testada com > 100 registros.
- [ ] Log não contém `app_key` nem `app_secret`.

**Bloqueio externo:** requer **credenciais Omie sandbox** — coordenar com o stakeholder.

---

### S6 — Gestão de Clientes BPO

**Objetivo:** CRUD de clientes com criptografia das credenciais e RBAC por `client_assignments`.

**Tarefas do backlog cobertas:**

- `[BACK 3.1]` Listar Clientes
- `[BACK 3.2]` Criar Cliente
- `[BACK 3.3]` Testar Conexão Omie
- `[BACK 3.4]` Editar Cliente
- `[BACK 3.5]` Reatribuir Cliente
- `[FRONT 3.7]` Modal Cadastro
- `[FRONT 3.8]` Modal Edição

**Pré-requisitos:** S1 (crypto), S2, S3 (auth), S5 (Omie client).
**Duração estimada:** 1 – 2 sessões (7 – 10 h).
**Referência doc:** `9. Gestão de Clientes BPO`.

**Entregáveis:**

1. **Backend `modules/clients/`:**
   - `GET /api/v1/clients?search=&page=&pageSize=` — admin vê todos; manager vê via `client_assignments`.
   - `POST /api/v1/clients` — recebe `name + app_key + app_secret`; **exige que a rota `test-connection` tenha sido chamada com sucesso** (não bloqueante no backend; o bloqueio está no front; mas aproveitar p/ **testar conexão atomicamente antes de persistir** — double check).
   - `POST /api/v1/clients/test-connection` — recebe `app_key + app_secret` em memória, chama `listar_clientes` mínimo, retorna `ok: boolean + error?`.
   - `PATCH /api/v1/clients/:id` — atualiza nome / status; credenciais apenas se enviadas (re-criptografa com novo IV). Manager só edita clientes da sua carteira.
   - `POST /api/v1/clients/:id/assign` — admin-only; atualiza `client_assignments.user_id`.
   - Lista de clientes para reatribuição: `GET /api/v1/users?role=manager&active=true`.

2. **Frontend:**
   - `/clientes` lista com colunas diferentes por role (admin mostra "Gerente Responsável").
   - Modal cadastro com "Testar conexão" desacoplado do submit (deve ser chamado e retornar ok antes de habilitar "Salvar").
   - Modal edição com credenciais placeholder `••••••••`; enviar campo vazio = manter.
   - Seletor de gerente para reatribuição (admin-only).

3. **Segurança:**
   - Endpoint test-connection **nunca** retorna as credenciais no response.
   - Criptografia via `core/crypto.py` (S1).
   - RBAC verificado em **cada** endpoint via dependency `require_client_access(client_id)`.

**DoD:**

- [ ] Cliente é criado com credenciais criptografadas (verificar no banco — nenhum plaintext).
- [ ] Manager não vê cliente de outro manager (teste com 2 contas).
- [ ] Editar cliente sem preencher credenciais mantém as antigas.
- [ ] Test-connection valida sem salvar.
- [ ] Reatribuir cliente invalida acesso do manager anterior (próxima request = 403).

---

### S7 — Detalhe do Cliente + Cache L1

**Objetivo:** tela de detalhe com histórico de conciliações e contas bancárias vindas do cache L1 (24 h) com "Sincronizar" manual.

**Tarefas do backlog cobertas:**

- `[BACK 4.1]` Endpoint Detalhe do Cliente e contas
- `[BACK 4.2]` Endpoint Histórico de Conciliações
- `[FRONT 4.3]` Tela Detalhe do Cliente

**Pré-requisitos:** S5, S6.
**Duração estimada:** 1 sessão (5 – 6 h).
**Referência doc:** `10. Detalhe de Clientes` + `5. Estratégia de Cache`.

**Entregáveis:**

1. **Cache L1 em `modules/clients/cache_service.py`:**
   - `get_accounts(client_id) -> list[BankAccount]`
   - Lógica: consulta `omie_accounts_cache` com `synced_at > now() - 24h`; se válido → retorna cache; senão → chama Omie, atualiza cache (upsert).
   - `force_sync(client_id)` bypassa TTL.

2. **Endpoints:**
   - `GET /api/v1/clients/:id` — detalhe básico + contas do cache.
   - `POST /api/v1/clients/:id/sync-accounts` — força sync.
   - `GET /api/v1/clients/:id/reconciliations?account_id=&month=&page=` — histórico paginado; inclui contadores (`conciliated_count`, etc.) + badge de status.

3. **Frontend `/clientes/[clientId]`:**
   - Header com nome + status + botões "Editar", "Sincronizar contas", "Nova Conciliação".
   - Seção contas: grid de cards + timestamp "Sincronizado há Xh".
   - Seção histórico: filtros (conta, mês), lista de cards, paginação.

**DoD:**

- [ ] Segunda chamada do detalhe dentro de 24 h **não** chama Omie (verificar em logs).
- [ ] Botão "Sincronizar" força chamada Omie e atualiza `synced_at`.
- [ ] Filtros de histórico funcionam.
- [ ] Card de sessão mostra contadores corretos.

---

### S8 — Formulário de Nova Conciliação + Validações

**Objetivo:** formulário + validações client-side + checagem de duplicata no servidor (apenas pelo hash, sem upload do arquivo).

**Tarefas do backlog cobertas:**

- `[FRONT 5.1]` Formulário Nova Conciliação
- `[FRONT 6.1]` Validações no Browser
- `[BACK 6.2]` Endpoint Verificação de Duplicata

**Pré-requisitos:** S7.
**Duração estimada:** 1 sessão (5 – 6 h).
**Referência doc:** `11. Nova Conciliação`.

**Entregáveis:**

1. **Frontend `/clientes/[id]/conciliacao/nova`:**
   - `react-hook-form` + zod:
     - `account_id`: required.
     - `reference_month`: required, ≤ mês atual.
     - `tolerance_days`: enum [1,2,3,5,7], default 3.
     - `file`: File, extensão em [pdf,csv,xls,xlsx], tamanho ≤ 20 MB.
   - Sequência de validação ao clicar "Processar":
     1. Campos obrigatórios.
     2. Extensão + tamanho.
     3. Hash SHA-256 via `crypto.subtle.digest` (Web Crypto API) — em `lib/crypto/hash.ts`, com streaming em chunks para arquivos grandes.
     4. `GET /check-duplicate?client_id=&omie_conta_id=&month=&hash=` → se duplicado, bloqueia com mensagem explicativa.
   - Loading states distintos ("Verificando...", "Processando...").

2. **Backend `POST /api/v1/reconciliations/check-duplicate`:**
   - Não recebe o arquivo, apenas os parâmetros.
   - Query rápida em `reconciliation_sessions` por `(bankAccountId, fileHash, reference_month)`.
   - Response: `{ duplicate: boolean, existing_session_id?: string }`.

**DoD:**

- [ ] Hash SHA-256 no browser bate com o calculado server-side em S9 (testar com arquivo conhecido).
- [ ] Arquivo de 19.9 MB passa; 20.1 MB bloqueado com mensagem.
- [ ] Duplicata detectada bloqueia upload e oferece link para sessão existente.
- [ ] Todas as mensagens conforme doc §11.

---

### S9 — Parsing do Arquivo via IA

**Objetivo:** envio do arquivo à Claude API com tool use estruturado, timeout agressivo, validação pós-IA, tela de preview.

**Tarefas do backlog cobertas:**

- `[BACK 7.1]` Endpoint Parsing via Claude
- `[FRONT 7.2]` Tela Validação do Parsing

**Pré-requisitos:** S8.
**Duração estimada:** 1 – 2 sessões (7 – 10 h).
**Referência doc:** `12. Parsing do Arquivo via IA`.

**Entregáveis:**

1. **`integrations/anthropic/client.py`:**
   - Cliente Claude com **modelo padrão `claude-sonnet-4-5`** (latência/custo) e opção `claude-opus-4-6` via parâmetro para arquivos complexos.
   - Modelos disponíveis e recomendações explicitados em `CLAUDE.md`.

2. **`integrations/anthropic/tools.py`** — definição do tool use:

   ```python
   EXTRACT_MOVEMENTS_TOOL = {
       "name": "extract_movements",
       "description": "...",
       "input_schema": {
           "type": "object",
           "properties": {
               "bank_name": {"type": "string"},
               "account_type": {"enum": ["checking", "credit_card"]},
               "period_start": {"type": "string", "format": "date"},
               "period_end": {"type": "string", "format": "date"},
               "opening_balance": {"type": "number"},
               "closing_balance": {"type": "number"},
               "transactions": {
                   "type": "array",
                   "items": {
                       "type": "object",
                       "properties": {
                           "date": {"type": "string", "format": "date"},
                           "description": {"type": "string"},
                           "amount": {"type": "number"},
                           "balance": {"type": "number"}
                       },
                       "required": ["date", "description", "amount"]
                   }
               }
           },
           "required": ["bank_name", "account_type", "period_start", "period_end", "transactions"]
       }
   }
   ```

   **`tool_choice = {"type": "tool", "name": "extract_movements"}`** para forçar o schema.

3. **`integrations/anthropic/prompts.py`** — prompt de sistema estável (para cache), instruindo extração exaustiva, preservação de descrição, normalização de datas ISO 8601, sinal no valor.

4. **Prompt caching** (Anthropic):
   - Bloco de sistema e schema marcados com `cache_control: {"type": "ephemeral"}`.
   - Reduz ~90 % do custo após 2ª chamada.

5. **Pré-processamento:**
   - PDF → texto via `pypdf` primeiro; se texto vazio (PDF escaneado), enviar PDF bruto como document block.
   - CSV / XLSX → converter para texto (cabeçalho + linhas) antes de enviar — mais barato que enviar binário.

6. **Endpoint `POST /api/v1/reconciliations/parse`:**
   - `multipart/form-data`: `file`.
   - Timeout 60 s (hard).
   - 1 retry em 5xx Claude.
   - **Validação pós-IA:**
     - Todas as datas parseáveis.
     - Valores são `Decimal` válidos.
     - `|sum(amount) − (closing − opening)| ≤ 0.01` (se os saldos vieram).
     - `transactions` não vazio.
     - Se falhar: `PARSE_ERROR`.
   - Retorna o objeto completo extraído + `preview = transactions[:5]`.

7. **Frontend:**
   - Após submit com sucesso, mostra tela preview na mesma rota (não navega).
   - Metadata + tabela 5 linhas + botões Cancelar / Confirmar.
   - Cancelar: limpa estado, nada foi salvo.
   - Confirmar: navega para `/conciliacao/{session_id}/processando` (mas a criação da sessão acontece em S10).

**DoD:**

- [ ] Arquivo PDF real de 50 linhas é extraído em < 30 s.
- [ ] Timeout de 60 s enforçado.
- [ ] Arquivo corrompido retorna PARSE_ERROR com mensagem clara.
- [ ] Prompt caching ativado (verificar `cache_read_input_tokens` no response da Claude).
- [ ] Preview exibe 5 linhas corretas.
- [ ] Cancelar não persiste nada.

---

### S10 — Processamento Automático em Background

**Objetivo:** sessão criada, job Celery processa, frontend faz polling, resultado classificado.

**Tarefas do backlog cobertas:**

- `[BACK 8.1]` Criar Sessão e Iniciar Job (8 subtarefas)
- `[BACK 8.2]` Background: Buscar Lançamentos Omie (Extrato)
- `[BACK 8.3]` Background: Buscar Lançamentos Omie (Pagar + Receber)
- `[BACK 8.4]` Background: Algoritmo de Cruzamento
- `[BACK 8.5]` Background: Criar Anomalias
- `[BACK 8.6]` Endpoint Polling de Status
- `[FRONT 8.7]` Tela Progresso

**Pré-requisitos:** S5 (Omie), S9 (parsing).
**Duração estimada:** 2 sessões (10 – 14 h). **Sessão mais complexa do projeto.**
**Referência doc:** `13. Processamento Automático`.

**Entregáveis:**

1. **`workers/celery_app.py`** — Celery com broker Redis, result backend Redis, task routes.

2. **`POST /api/v1/reconciliations`** (criar sessão):
   - Recebe payload do preview + `omie_conta_id + reference_month + tolerance_days + file_hash + transactions[]`.
   - Em **transação atômica**:
     - Insere `reconciliation_sessions(status='processing')`.
     - Insere `reconciliation_file_entries[]` (descrição criptografada, data/valor em claro, `situation='sem_omie'`).
   - Enfileira `process_reconciliation_task.delay(session_id)`.
   - Retorna `{ session_id, status: 'processing' }`.

3. **Task `process_reconciliation_task(session_id)`:**

   ```python
   @celery_app.task(bind=True, max_retries=3, autoretry_for=(OmieTimeoutError,), retry_backoff=True)
   async def process_reconciliation_task(self, session_id: str):
       # 1. Load session + entries
       # 2. Descriptografar credenciais do cliente (in-memory)
       # 3. Etapa 2: ListarExtrato (período expandido)
       # 4. Etapa 3: ListarContasPagar (ATRASADO + PREVISTO) + ListarContasReceber
       # 5. Etapa 4: algoritmo de matching
       # 6. Etapa 5: criar anomalias estruturais
       # 7. Etapa 6: recalcular saldos + status='reviewing'
   ```

   - **Etapa 4 (matcher — função pura):**
     - Indexar `omie_entries` por `amount` (dict de lista).
     - Para cada `file_entry`: buscar candidates com `|a1-a2|≤0.01` e `|d1-d2|≤tol`, excluir já consumidos.
     - Desempate: `|days_diff|`, depois `|amount_diff|`, depois `date asc`.
     - Atualizar `omie_lancamento_id` e `situation='conciliado'`.
   - **Etapa 5 (anomalias):**
     - Cada linha `sem_omie` → `missing_in_omie`.
     - Cada Omie Atrasado sem match → `missing_in_file`.
   - **Etapa 6 (finalizar):** calcular todos os contadores e saldos, `status='reviewing'`, `processed_at=now()`.

4. **Error handling:**
   - `OmieAuthError` → `status='error'`, `error_message='Credenciais Omie inválidas'`.
   - `OmieTimeoutError` → retry automático até 3 vezes com backoff; após esgotar, `status='error'`.
   - Erro inesperado → `status='error'`, loggar no Sentry com session_id.

5. **`GET /api/v1/reconciliations/:id/status`:**
   - Retorna `{ status, error_message?, counts: {...}, current_step: 'fetching_omie' | 'matching' | ... }`.
   - Rate limit 1 req/s por session_id (não DOS).

6. **Frontend `/conciliacao/[sessionId]/processando`:**
   - Steps visuais (conforme doc §13.1).
   - Polling 3s via TanStack Query `refetchInterval`.
   - Redirect automático para `/conciliacao/[sessionId]` quando `status='reviewing'`.
   - Se `status='error'` → mostra mensagem + botão "Voltar ao formulário".

**DoD:**

- [ ] Conciliação completa de arquivo de 100 linhas + 80 lançamentos Omie em < 90 s.
- [ ] Matcher testado unitariamente com: valores iguais em datas diferentes, valores iguais consecutivos, centavos, edge case de tolerância no limite.
- [ ] Double-allocation evitada (Omie consumido não matcha de novo).
- [ ] Retry em timeout Omie funciona (simular com respx delay).
- [ ] Status passa por todos os steps corretamente no polling.

---

### S11 — Tela de Revisão: Backend + Cache L2

**Objetivo:** todos os endpoints que a tela de revisão consome, com cache abstrato de lançamentos Omie.

**Tarefas do backlog cobertas:**

- `[BACK 9.1]` Listar Movimentações
- `[BACK 9.2]` Dados Omie de Lançamentos (batch)
- `[BACK 9.3]` Atualizar Ação em Linha
- `[BACK 9.4]` Lançamentos Disponíveis (para Trocar)
- `[BACK 9.5]` Listar Divergências Omie
- `[BACK 9.6]` Atualizar Ação em Divergência
- `[BACK 9.7]` Listar Anomalias
- `[BACK 9.8]` Registrar Anomalia
- `[BACK 9.9]` Resolver Anomalia
- `[BACK 9.10]` Listar Tipos de Anomalia

**Pré-requisitos:** S10.
**Duração estimada:** 2 sessões (10 – 12 h).
**Referência doc:** `14. Tela de Revisão`.

**Entregáveis:**

1. **Cache L2 abstrato em `app/cache/`:**

   ```python
   class AsyncCache(Protocol):
       async def get(self, key: str) -> bytes | None: ...
       async def set(self, key: str, value: bytes, ttl_seconds: int) -> None: ...
       async def delete(self, key: str) -> None: ...
       async def mget(self, keys: list[str]) -> dict[str, bytes]: ...
   ```

   - `InMemoryCache` com dict + expiresAt + limpeza periódica.
   - `RedisCache` (comentada, pronta para trocar via env `CACHE_BACKEND=memory|redis`).
   - **Chave:** `omie_lancamento:{client_id}:{omie_lancamento_id}`, TTL 2 h.
   - `invalidate_on_write` — se o analista troca o match, invalidar chave associada.

2. **Endpoints:**
   - `GET /reconciliations/:id/file-entries?situation=&type=&search=&page=&pageSize=` → paginado, com descrição descriptografada.
   - `GET /reconciliations/:id/omie-lancamentos?ids=id1,id2,...` → **batch enrich** usando cache L2, buscando do Omie apenas miss.
   - `PATCH /reconciliations/:id/file-entries/:entryId` → body `{ user_action, note?, new_omie_lancamento_id? }`. Valida transições (§17 doc). Em caso de `new_omie_lancamento_id`: valida não-duplicata na sessão.
   - `GET /reconciliations/:id/available-omie?search=&not_used=true` → lançamentos não vinculados, para o modal "Trocar".
   - `GET /reconciliations/:id/omie-entries?status=&page=` → divergências Omie.
   - `PATCH /reconciliations/:id/omie-entries/:entryId` → ação do analista.
   - `GET /reconciliations/:id/anomalies?severity=&resolved=&page=`
   - `POST /reconciliations/:id/anomalies` (registro manual)
   - `PATCH /reconciliations/:id/anomalies/:anomalyId/resolve` (resolve com nota obrigatória ≥ 10 chars)
   - `GET /api/v1/anomaly-types?active=true` (para o modal de registrar)

3. **Autorização em **todos\*\*: `require_client_access(client_id)` via dependency que busca a session → client_id e verifica.

**DoD:**

- [ ] Cache L2 hit/miss logado e métrica gerada.
- [ ] Troca de match invalida cache da chave antiga.
- [ ] Lista de file-entries com 500 linhas retorna em < 500 ms.
- [ ] Transição de estado inválida (`conciliado → sem_omie` sem restaurar) retorna 400.
- [ ] Anomalia resolvida requer nota ≥ 10 chars.

---

### S12 — Tela de Revisão: Estrutura + Aba Movimentações

**Objetivo:** esqueleto da tela de revisão com 4 abas e a primeira aba (Movimentações) totalmente funcional, incluindo modais.

**Tarefas do backlog cobertas:**

- `[FRONT 9.11]` Estrutura da Tela (5 sub)
- `[FRONT 9.12]` Aba 1 — Movimentações
- `[FRONT 9.13]` Modal Trocar Lançamento
- `[FRONT 9.14]` Modal Registrar Anomalia

**Pré-requisitos:** S11.
**Duração estimada:** 2 sessões (10 – 13 h).
**Referência doc:** `14. Tela de Revisão §14.1-14.3, 14.5`.

**Entregáveis:**

1. **Layout `/clientes/[cid]/conciliacao/[sid]/page.tsx`:**
   - Header fixo com breadcrumb, contadores, botão Exportar.
   - Tabs (shadcn/ui Tabs) com lazy-load de conteúdo.
   - Contexto de sessão em provider (dados compartilhados).

2. **Aba 1 — Movimentações:**
   - Filtros (situação, tipo, busca com debounce 300 ms).
   - `@tanstack/react-table` + `react-virtual` para performance com > 500 linhas.
   - Enriquecimento com dados Omie (fornecedor, categoria) via `useQuery` batch que chama `/omie-lancamentos?ids=...` a cada página.
   - Ações condicionais por situação (Confirmar, Trocar, Anotar, Ignorar, Restaurar, Registrar anomalia).

3. **Modal "Trocar lançamento Omie":**
   - Tabela filtrável de candidates (chamando `/available-omie`).
   - Seleção com highlight.
   - Submit chama PATCH e invalida cache da linha + da aba.

4. **Modal "Registrar anomalia":**
   - Select de tipos (ordenado por severidade).
   - Textarea opcional.
   - Submit chama POST, fecha modal, atualiza contador no header.

**DoD:**

- [ ] Rolagem virtual fluida com 2 000 linhas.
- [ ] Filtros aplicados alteram contadores visíveis.
- [ ] Modal "Trocar" atualiza linha com novo fornecedor/categoria sem recarregar.
- [ ] Ações persistem e sobrevivem a reload (GET re-buscando).

---

### S13 — Tela de Revisão: Abas Restantes

**Objetivo:** abas 2, 3 e 4.

**Tarefas do backlog cobertas:**

- `[FRONT 9.15]` Aba 2 — Divergências Omie
- `[FRONT 9.16]` Aba 3 — Anomalias
- `[FRONT 9.17]` Aba 4 — Resumo

**Pré-requisitos:** S12.
**Duração estimada:** 1 – 2 sessões (9 – 11 h).
**Referência doc:** `14. Tela de Revisão §14.4, 14.6, 14.7`.

**Entregáveis:**

1. **Aba 2 — Divergências Omie:**
   - Tabela com colunas conforme doc §14.4.
   - Enriquecimento Omie (fornecedor/categoria).
   - Ações Marcar/Ignorar/Anotar/Registrar anomalia.

2. **Aba 3 — Anomalias:**
   - Filtros severidade + status.
   - Ordenação críticas → moderadas → info; pendentes antes.
   - Modal de resolução com nota ≥ 10 chars.

3. **Aba 4 — Resumo:**
   - Cards com saldos por mês.
   - Indicadores agregados.
   - Resumo de anomalias.
   - Dados vindos diretamente da session (sem chamar Omie novamente).

**DoD:**

- [ ] Status de saldo (verde/amarelo/vermelho) calculado corretamente.
- [ ] Anomalia pendente sem nota não pode ser resolvida.
- [ ] Contador no header atualiza ao resolver.

---

### S14 — Exportação do Relatório Excel

**Objetivo:** botão Exportar gera Excel de 5 abas e serve como download direto.

**Tarefas do backlog cobertas:**

- `[BACK 10.1]` Endpoint Gerar Relatório (20 subtarefas — cada aba + formatação)

**Pré-requisitos:** S13.
**Duração estimada:** 1 – 2 sessões (8 – 10 h).
**Referência doc:** `15. Exportação do Relatório Excel`.

**Entregáveis:**

1. **`modules/reports/service.py`** com função pura `build_report_workbook(session_data) -> BytesIO`:
   - Aba 1 — Resumo (título + saldos + indicadores + anomalias).
   - Aba 2 — Movimentação × Lançamento (cores por situação).
   - Aba 3 — Divergências Omie (cores por status).
   - Aba 4 — Sem Omie.
   - Aba 5 — Anomalias (ordenação crítica > moderada > info).
   - Formatação: BRL, datas, wrap, larguras auto.

2. **`POST /api/v1/reconciliations/:id/export`:**
   - **Decisão:** síncrono (gera em memória, retorna stream) OU assíncrono (enqueue → link).
   - **Recomendação:** **síncrono** para MVP (até 10k linhas é rápido). Migrar para assíncrono se necessário.
   - Response: `StreamingResponse` com `Content-Disposition: attachment; filename="Conciliacao_{cliente}_{conta}_{mes}.xlsx"`.
   - Nome sanitizado (remover espaços, acentos, caracteres especiais).

3. **Frontend:**
   - Botão "Exportar Relatório" com estado "Gerando..."; faz fetch + `window.URL.createObjectURL(blob)` + download trigger.
   - Toast de erro se falhar.

**DoD:**

- [ ] Excel abre corretamente no Excel/LibreOffice/Google Sheets.
- [ ] Cores por situação/status conforme doc.
- [ ] Nome do arquivo sem caracteres inválidos.
- [ ] Valores BRL formatados corretamente.
- [ ] Notas descriptografadas aparecem nas abas 2, 3, 5.

---

### S15 — Gestão de Tipos de Anomalia

**Objetivo:** tela de admin para ativar/desativar tipos (Fase 1 apenas).

**Tarefas do backlog cobertas:**

- `[BACK 11.1]` CRUD de Tipos de Anomalia
- `[FRONT 11.2]` Tela de Gestão

**Pré-requisitos:** S3, S4.
**Duração estimada:** 1 sessão (5 – 6 h).
**Referência doc:** `16. Gestão de Tipos de Anomalia`.

**Entregáveis:**

1. **Backend `modules/anomalies/types_routes.py`:**
   - `GET /api/v1/anomaly-types` (todos, paginado, busca) — admin.
   - `POST /api/v1/anomaly-types/:id/deactivate`
   - `POST /api/v1/anomaly-types/:id/activate`
   - Fase 2 (não implementar): `POST /api/v1/anomaly-types` (criar custom).

2. **Frontend `/configuracoes/anomalias`:**
   - Tabela ordenada por severidade.
   - Badge de severidade.
   - Toggle ativo/inativo com modal de confirmação.

**DoD:**

- [ ] Tipo desativado não aparece no modal "Registrar anomalia" (S12).
- [ ] Anomalias existentes do tipo desativado continuam visíveis.

---

### S16 — Hardening de Segurança

**Objetivo:** aplicar as regras de §5 no sistema inteiro (pode rodar em paralelo com S11-S15).

**Tarefas do backlog cobertas:** — (transversal).
**Pré-requisitos:** S3.
**Duração estimada:** 1 sessão (5 h).

**Entregáveis:**

1. **Headers de segurança** no Next.js `middleware.ts` + FastAPI middleware:
   - CSP restritivo com nonce.
   - HSTS, nosniff, deny-frames, strict referrer.
2. **CSRF** via double-submit cookie em todas as mutations.
3. **Rate limit** global (slowapi com Redis backend).
4. **Magic bytes check** em upload (já mencionado em S1 — integrar em S9).
5. **Sanitização** de descrições exibidas (frontend usa React auto-escape, só revisar).
6. **pip-audit + npm audit** no CI como gate.
7. **Script `scripts/rotate-encryption-key.py`** (rotaciona `OMIE_ENCRYPTION_KEY` — re-criptografa todos os registros, zero downtime com dual-key pattern).
8. **Backup automatizado** (`pg_dump | gpg | aws s3 cp`).
9. **Pentest checklist** manual (OWASP ASVS L1).

**DoD:**

- [ ] CI falha em PR com dep crítica vulnerável.
- [ ] Headers verificados em `curl -I`.
- [ ] Rate limit dispara 429 em teste de carga local.
- [ ] Key rotation testada em dev.

---

### S17 — Observabilidade e Logs Estruturados

**Objetivo:** visibilidade operacional suficiente para operar em produção.

**Pré-requisitos:** S1 (logging base).
**Duração estimada:** 1 sessão (4 – 5 h).

**Entregáveis:**

1. **Sentry** (frontend + backend) com `release` e `environment`.
2. **Correlation ID** propagado entre API → workers (via Celery header).
3. **Métricas chave exportadas** (Prometheus ou StatsD):
   - `reconciliation_parse_duration_seconds` (histograma).
   - `reconciliation_matching_duration_seconds`.
   - `omie_call_duration_seconds` (por endpoint).
   - `cache_l2_hit_rate`.
   - `reconciliation_status_count` (gauge por status).
4. **Health checks:** `GET /health` (API), `GET /health/ready` (DB + Redis reachable).
5. **Dashboard Grafana básico** (template JSON).

**DoD:**

- [ ] Erro simulado aparece no Sentry.
- [ ] Log de uma conciliação completa é correlacionável do submit → worker → resultado via `correlation_id`.

---

### S18 — Testes E2E, Documentação e Deploy

**Objetivo:** qualidade final, docs operacionais e pipeline de deploy reprodutível em staging e produção. A plataforma específica (Fly, Render, AWS, etc.) **fica em aberto** — a sessão entrega artefatos agnósticos de plataforma (imagens Docker + workflows com hook configurável) e o **Anexo S18.A** lista as opções com prós/contras para decisão antes da execução desta sessão.

**Pré-requisitos:** S14 (fluxo principal funcionando), S16 (hardening), S17 (observabilidade).
**Duração estimada:** 3 – 4 sessões (14 – 18 h). Aumentou em relação à versão original porque agora inclui infra-as-code, runbooks reais, key rotation testada e gate end-to-end contra Omie real (não mais mock).

> **Lembrete crítico antes de começar S18:** confirmar com Pedro/Galhardo se as **credenciais Omie reais** já foram disponibilizadas. Sem elas, o "deploy em staging completo" no DoD não é verificável — vira deploy com mocks, que não comprova nada. _(ver §13)_

---

#### S18.1 — Testes E2E e Contract Tests

**Entregáveis:**

1. **Playwright E2E** (apps/web/e2e/) cobrindo 3 fluxos críticos:
   - **Auth & RBAC:** login → manager NÃO vê cliente de outro manager → admin vê todos.
   - **Onboarding cliente:** novo cliente → test connection (mock Omie OK) → sync_accounts → cliente listado com contas.
   - **Conciliação end-to-end:** upload PDF fake → preview 5 linhas → confirmar → polling status → tela de revisão → ações (vincular manual, criar anomalia) → export Excel → assert hash do arquivo gerado.
2. **Contract tests Omie** (apps/api/tests/contract/) com `respx`:
   - Snapshots versionados de payloads reais (anonimizados) de cada endpoint usado: `ListarContasCorrentes`, `ListarExtrato`, `ListarContasPagar`, `ListarContasReceber`, `ListarClientes`.
   - Cada snapshot tem um teste que faz `model_validate` e checa que os campos esperados sobrevivem — pega quando Omie muda contrato sem avisar.
3. **Smoke test pós-deploy** (`scripts/smoke_test.py`): após cada deploy, hits no `/health` e `/health/ready` + uma chamada autenticada de listagem de clientes. CI falha se smoke falhar e dispara rollback (ver S18.3).

**DoD parcial S18.1:**

- [ ] Playwright roda em CI em < 5 min com `--reporter=github`.
- [ ] Cada endpoint Omie tem ≥ 1 snapshot de payload real + teste de schema.
- [ ] Smoke test passa contra ambiente local (compose) e contra staging.

---

#### S18.2 — Build artifacts (Docker prod-ready)

**Estado atual:** existem `docker/Dockerfile.api` e `docker/Dockerfile.web` multi-stage funcionais para dev. O `docker-compose.yml` é dev-only (volume bind no código, worker desligado).

**Entregáveis:**

1. **Refinar Dockerfiles para prod:**
   - `docker/Dockerfile.api`: garantir `--no-dev` no `uv sync`, fixar versão do uv (`COPY --from=ghcr.io/astral-sh/uv:0.5.x`), label `org.opencontainers.image.{source,version,revision}`, `STOPSIGNAL SIGTERM` explícito.
   - `docker/Dockerfile.web`: confirmar `output: 'standalone'` no `next.config.mjs`, build args `NEXT_PUBLIC_*` injetados em build-time, telemetria desligada.
   - **Mesma imagem da API serve para worker** — `CMD` é sobrescrito no compose/plataforma (`arq app.workers.arq_worker.WorkerSettings`).
2. **`docker-compose.prod.yml`** (no diretório `docker/`):
   - Sem volume bind no código (a imagem É o código).
   - Worker habilitado por padrão (sem `profiles: ["workers"]`).
   - Sem portas expostas no host pra `postgres` e `redis` (apenas rede interna).
   - Health checks em todos os serviços com `start_period` realista.
   - `restart: unless-stopped` em todos.
   - **Sem valores de fallback `change_me_in_dev_only`** — se a env var não vier, o compose deve falhar (`${VAR:?VAR is required}`).
   - Útil pra: rodar local de prod (debugging), deploy em VPS single-box (Hetzner/DO droplet), staging mínimo. **Não é** a única forma de deploy possível — plataformas como Fly têm seu próprio `fly.toml` que substitui o compose.
3. **OpenAPI export automático:**
   - `apps/api/scripts/export_openapi.py` gera `packages/shared-types/openapi.json` a partir do `app.main:app`.
   - Step no CI roda esse script e roda `openapi-typescript packages/shared-types/openapi.json -o packages/shared-types/api-types.ts`.
   - PR de divergência (CI falha se o tipo gerado mudou e não foi commitado) — força sincronização front/back.

**DoD parcial S18.2:**

- [ ] `docker build -f docker/Dockerfile.api .` resulta em imagem ≤ 250 MB.
- [ ] `docker build -f docker/Dockerfile.web .` resulta em imagem ≤ 200 MB.
- [ ] `docker compose -f docker/docker-compose.prod.yml up` sobe stack completa sem volume bind.
- [ ] Imagens rodam como **usuário não-root** (já estão; manter verificado).
- [ ] CI gera `api-types.ts` e falha se houver drift.

---

#### S18.3 — Pipeline de deploy (CI/CD)

**Princípio:** o workflow do GitHub Actions faz `build + push para registry + chama hook de deploy específico da plataforma`. O hook é a **única parte que muda** entre plataformas — o resto é genérico.

**Entregáveis:**

1. **`.github/workflows/deploy.yml`** (separado do CI):
   - **Triggers:** `push` em `main` → deploy automático para `staging`. `workflow_dispatch` com input `environment=production` → deploy manual para prod.
   - **Jobs em ordem:**
     1. `build-and-push-api` — `docker buildx build --platform linux/amd64` → push para registry (GHCR por padrão, configurável via secret). Tags: `sha-${{ github.sha }}` + `latest-${env}`.
     2. `build-and-push-web` — idem para a imagem web.
     3. `run-migrations` — roda `alembic upgrade head` contra o DB do environment alvo. Bloqueia o restante se falhar.
     4. `deploy` — chama o **hook da plataforma** (ver abaixo).
     5. `smoke-test` — `scripts/smoke_test.py` contra o env recém-deployado. Falha aqui dispara `rollback`.
     6. `rollback` (condicional) — re-aplica a tag `sha-${{ github.event.before }}` da imagem anterior.
   - **Concurrency:** `group: deploy-${{ inputs.environment }}` com `cancel-in-progress: false` (nunca matar deploy no meio).
2. **Hook de deploy parametrizado** (`.github/workflows/_deploy_hook.yml`, reusable workflow):
   - Recebe `image_tag`, `environment` como inputs.
   - Implementa **uma** das estratégias (a decidir, ver Anexo S18.A):
     - **Fly.io:** `flyctl deploy --image $IMAGE_TAG --strategy rolling --app auditoria-api-${env}` (idem worker, idem web).
     - **Render:** `curl -X POST $RENDER_DEPLOY_HOOK_URL` (configura imagem por env var).
     - **AWS ECS:** `aws ecs update-service --task-definition ... --force-new-deployment`.
   - Trocar plataforma = trocar este arquivo, sem mexer no `deploy.yml` principal.
3. **Estratégia de release das migrations:**
   - Migrations rodam **antes** do deploy do código novo, contra o DB compartilhado pelo env. Falha em migration aborta deploy.
   - **Regra de ouro:** toda migration precisa ser compatível com a versão N e N-1 do código (additive-only durante uma janela, depois cleanup numa migration seguinte). Documentar isso no `docs/runbook.md`.
   - Worker e API sobem na mesma versão (mesma imagem) — não há janela em que worker novo bata num schema velho.
4. **GitHub Environments:**
   - `staging` (auto-deploy de main) e `production` (manual + required reviewer).
   - Secrets segregados por environment — nunca compartilhar `OMIE_ENCRYPTION_KEY` entre staging e prod.

**DoD parcial S18.3:**

- [ ] Push em main → staging deployado automaticamente em < 8 min (build + push + migrate + deploy + smoke).
- [ ] `workflow_dispatch` com `environment=production` exige aprovação manual configurada no GitHub Environment.
- [ ] Smoke test falho aciona rollback automático para a imagem anterior.
- [ ] Migration falha aborta deploy (código novo nunca sobe contra DB com schema incompatível).

---

#### S18.4 — Runtime concerns (secrets, health, backup, key rotation)

1. **Estratégia de secrets** (genérica; mapeamento por plataforma no Anexo):
   - **Source of truth:** secret manager da plataforma (Fly Secrets, Render Env Groups, AWS Secrets Manager). Nunca em `.env` commitado.
   - **GitHub Actions** acessa via OIDC quando possível (sem long-lived tokens); fallback é GitHub Encrypted Secrets.
   - **Lista de secrets obrigatórios por env**: `OMIE_ENCRYPTION_KEY` (32 bytes hex), `JWT_SECRET` (64 bytes), `ANTHROPIC_API_KEY`, `DATABASE_URL`, `REDIS_URL`, `SENTRY_DSN_API`, `SENTRY_DSN_WEB`. Documentar em `docs/runbook.md` com formato esperado de cada um.
2. **Endpoints de saúde** (alguns já existem do S17 — confirmar e estender):
   - `GET /health` — liveness, sempre 200 se o processo está vivo. Usado por orquestrador.
   - `GET /health/ready` — readiness: faz `SELECT 1` no DB e `PING` no Redis. 503 se qualquer um falhar. Plataforma usa pra decidir quando rotear tráfego.
   - **Worker:** ARQ não tem HTTP por default. Adicionar `apps/api/scripts/worker_healthcheck.py` que escreve heartbeat num key Redis com TTL 60s; plataforma roda esse script como healthcheck.
3. **Backups automatizados** (Postgres):
   - Cron diário (3h BRT): `pg_dump -Fc` → gzip → upload pra object storage (S3/R2/GCS — escolha junto com a plataforma).
   - Retenção: 30 dias diários + 12 mensais. Implementar via lifecycle rule no bucket.
   - Backup encriptado **client-side** com GPG antes do upload (key separada do `OMIE_ENCRYPTION_KEY`).
   - Script: `apps/api/scripts/backup_postgres.sh`. Roda como CronJob na plataforma (Fly: `fly machine run --schedule daily`; AWS: EventBridge → ECS task).
   - **Teste de restore mensal** documentado no runbook — backup que nunca foi restaurado não é backup.
4. **Key rotation** (re-aproveita `scripts/rotate-encryption-key.py` do S16):
   - Documentar procedimento no runbook: gera key nova → modo dual-key ativado via env var → re-encripta todos os registros → desativa key velha → remove env var de transição.
   - Smoke test pós-rotation: criar cliente novo com creds Omie + reabrir cliente existente; ambos devem decifrar.
5. **Domínios e TLS:**
   - `app.auditoria.hologram.com.br` (front) + `api.auditoria.hologram.com.br` (API), ou subdomínios definidos pela Hologram.
   - TLS via Let's Encrypt automático (todas as 3 plataformas candidatas suportam).
   - `staging.app.auditoria.hologram.com.br` para o env de staging.
   - **CORS** na API restringido aos domínios reais (sem `*`).

**DoD parcial S18.4:**

- [ ] Nenhum secret aparece em `git log`, `docker history`, logs de CI ou logs de runtime (grep automático no CI).
- [ ] `/health` e `/health/ready` retornam payloads esperados em 3 envs (local/staging/prod).
- [ ] Backup diário aparece no bucket por 7 dias seguidos sem intervenção.
- [ ] Restore de backup arbitrário funciona em ambiente isolado (documentar no runbook).
- [ ] Key rotation executada em staging end-to-end sem downtime perceptível.

---

#### S18.5 — Documentação final

1. **`README.md` (raiz)** — onboarding em < 15 min:
   - Pré-requisitos (Docker, uv, pnpm via corepack, Postgres opcional local).
   - Comandos pra subir dev (`docker compose -f docker/docker-compose.yml up -d postgres redis` + dois `uv run` e um `pnpm dev`).
   - Como rodar testes, lint, mypy.
   - Link pro CLAUDE.md, runbook, api docs.
2. **`docs/runbook.md`** — 10+ cenários de incidente:
   - "Omie API fora — como segurar fila e comunicar usuário."
   - "Fila ARQ travada — diagnóstico e drain."
   - "Key rotation — passo a passo."
   - "Rollback de deploy — quando automático falha."
   - "DB lento — queries para investigar (`pg_stat_activity`, locks)."
   - "Recuperar backup específico — comando exato + checklist de validação."
   - "Vazamento suspeito de credencial — playbook (rotate immediately, audit log, comunicar Pedro)."
   - "Usuário desativado ainda consegue logar — debug do JWT middleware."
   - "Reconciliação 'travada' em `processing` — quando reprocessar vs descartar."
   - "Custos Claude explodindo — investigação + budget alert."
3. **`docs/api.md`** — gerada do OpenAPI exportado em S18.2. Build no CI publica HTML estático (ReDoc) num path `/api/docs` da API ou separado.
4. **`docs/architecture.md`** — diagrama do fluxo (já existe `Docs/flow/Fluxograma.png`; consolidar + diagrama de deploy com a plataforma escolhida).
5. **`CLAUDE.md`** — atualizar §2 (Infra) com a plataforma escolhida e §8 (comandos) com os comandos de deploy. Remover "AWS ECS / Docker Swarm (prod — a decidir)" → substituir pelo decidido.
6. **Decision record** em `Docs/decisions/0001-deploy-platform.md` — ADR justificando a plataforma escolhida, alternativas consideradas, trade-offs aceitos.

**DoD parcial S18.5:**

- [ ] Onboarding cego: um dev novo (ou Claude num projeto fresh) consegue subir o dev seguindo só o README, sem perguntar nada.
- [ ] Runbook tem ≥ 10 cenários, cada um com comandos exatos.
- [ ] OpenAPI export está sincronizado em todo PR.
- [ ] ADR de plataforma commitado em `Docs/decisions/`.

---

#### DoD agregado da S18

- [ ] Todos os DoD parciais (S18.1 a S18.5) ✅.
- [ ] Push em main faz deploy completo em staging em < 8 min sem intervenção.
- [ ] Smoke test passa em staging.
- [ ] Pelo menos 1 deploy manual de production foi executado e auditado.
- [ ] Sentry recebeu eventos reais de staging e prod (não só synthetic).
- [ ] Onboarding cego validado por terceiro.

---

#### Anexo S18.A — Plataformas candidatas

> A escolha entre essas plataformas é uma conversa separada com a Hologram (latência BR, budget, comfort do time com cada stack). Esta sessão entrega artefatos agnósticos; a integração específica vai num hook de deploy parametrizado (ver S18.3). Os 3 candidatos abaixo são os que sobreviveram à triagem inicial (Heroku ficou de fora pelo preço e Vercel-only não atende worker + Postgres BR).

##### Opção A — Vercel (web) + Fly.io (api/worker) + Neon (Postgres) + Upstash (Redis)

**Características:**

- **Região:** Fly `gru` (São Paulo) + Neon `sa-east-1`. Latência mínima para Omie.
- **DX:** deploy via `git push` em todos os 4 componentes. `fly.toml` versionado.
- **Custo estimado:** US$ 30 – 70/mês começando, escala linear com uso.
- **Workers:** Fly Machines suportam workers persistentes nativamente.
- **Backups Postgres:** Neon faz PITR automático (até 7 dias no free, 30 dias no pago).

**Trade-offs:**

- 4 dashboards diferentes (Vercel, Fly, Neon, Upstash) — mais lugares para olhar, menos lugares para configurar.
- Fly tem reputação de instabilidade ocasional em incidents — mitigado por staging idêntico a prod.
- Free tier suficiente para staging; produção precisa upgrade em pelo menos Fly + Neon.

**Setup mínimo:**

- 3 apps no Fly: `auditoria-api-staging`, `auditoria-worker-staging`, `auditoria-web` (se quiser SSR no Fly em vez de Vercel — alternativa).
- 1 projeto Neon com branches `main` (prod) e `staging`.
- 1 banco Upstash compartilhado entre staging/prod com keys separadas.

##### Opção B — Render (tudo)

**Características:**

- **Região:** `oregon`, `frankfurt`, `singapore`. **Sem BR** — latência Omie ~150ms × 4 chamadas ≈ +600ms por conciliação.
- **DX:** uma única plataforma com `render.yaml` versionado descreve API + Worker + Web + Postgres + Redis.
- **Custo estimado:** US$ 25 – 60/mês começando. Free tier dorme após 15min — não serve para staging real.
- **Workers:** Background Worker é um tipo de service first-class.
- **Backups Postgres:** snapshot diário automático, retenção 7 dias no plano starter.

**Trade-offs:**

- Sem região BR. Aceitar +600ms de latência por conciliação. Para uso interno com analista esperando ~30s, é tolerável.
- Render é mais "magic" — menos controle quando algo dá errado.
- Migração para outro lugar é fácil porque é tudo Docker padrão.

##### Opção C — AWS sa-east-1 (ECS Fargate + RDS + ElastiCache + S3 + CloudFront)

**Características:**

- **Região:** `sa-east-1` (São Paulo). Latência mínima.
- **DX:** Terraform/CDK obrigatório. ECR para imagens. Secrets Manager nativo. IAM, VPC, ALB, target groups.
- **Custo estimado:** US$ 80 – 150/mês mesmo com tráfego mínimo (RDS + Fargate + NAT Gateway pesam no baseline).
- **Workers:** ECS Service com `desired_count` separado.
- **Backups:** RDS automated backups + snapshots manuais.

**Trade-offs:**

- 5–10x mais código de infra que as outras opções. Justifica-se se Hologram já é AWS-shop ou se há requisitos de compliance específicos (ex.: cliente final exige hospedagem AWS BR).
- Curva de aprendizado alta — operação é trabalho contínuo.
- Pode ser destino futuro (migrar de Opção A para C quando virar produto sério) sem reescrever nada — tudo já é Docker.

##### Recomendação preliminar

**Opção A** (Vercel + Fly + Neon + Upstash) para o MVP, com migração planejada para **Opção C** se/quando virar produto externo da Hologram. Razões:

1. Única que mantém latência BR em todos os componentes.
2. Custo controlável.
3. Deploy via `git push`, sem Terraform.
4. Pode-se trocar pra Opção C sem reescrever nada — é só trocar o hook de deploy e o destino dos secrets.

**Próxima decisão necessária antes de iniciar S18:** Pedro confirma com a Hologram qual opção seguir, abre ADR `Docs/decisions/0001-deploy-platform.md` registrando a escolha e o S18 pode começar.

---

### S19 — Qualificação Inteligente de Lançamentos

**Origem:** pedido de sócio da Hologram, posterior ao plano original. A reconciliação atual (S10–S14) só valida **valor + data** (CLAUDE.md §5.1–§5.2). Esta sessão adiciona uma camada **semântica + histórica** sobre os pares já conciliados — pega casos como _"TARIFA BANCÁRIA"_ classificada no Omie como _"Pagamento de Cartão"_.

**Objetivo:** detectar inconsistência de classificação entre o que o extrato bancário descreve e o que o Omie tem como fornecedor/categoria. Gerar anomalias auditáveis pelo analista. Permitir que tendências e desvios apareçam em relatórios futuros (Fase 2).

**Pré-requisitos:** S14 (export Excel) + S15 (tipos de anomalia framework).
**Duração estimada:** 2 sessões (12 – 16 h). Pode rodar em paralelo com S16/S17; deve estar pronto antes do deploy final (S18) se for parte do MVP. Caso a Hologram decida adiar, vira release "1.1" pós-deploy.

> **Decisão pendente:** S19 entra no MVP ou fica para release pós-deploy? Impacta o cronograma do S18.

---

#### Escopo MVP — 3 camadas de análise

##### Camada 1 — Verificação semântica via IA (Claude)

Para cada par `(file_entry, omie_entry)` conciliado, monta tupla `(descricao_extrato, fornecedor_omie, categoria_omie)`. Bate em lote (50 pares por chamada, prompt caching ativo — CLAUDE.md §7) no Claude com prompt estruturado pedindo classificação `ok | suspeita | incoerente`. Custo estimado: < US$ 0.05/sessão com cache. Resultado vira anomalias `qualificacao_suspeita` (severity moderate) e `qualificacao_incoerente` (severity high).

##### Camada 2 — Padrão histórico (SQL determinístico — sem IA, sem custo)

Para cada par, query nas últimas 3 conciliações `reviewing|done` do mesmo cliente. Agrega por `(supplier, category)`. Se categoria atual ≠ moda histórica AND moda tem ≥ 2 ocorrências → flag `padrao_quebrado` (severity low). Cenário típico: fornecedor _"MOINHO PRADO"_ sempre classificado como _"Material de Construção"_, mas dessa vez veio _"Tarifa"_.

##### Camada 3 — Outliers de valor (SQL determinístico)

Para cada par, calcula `avg ± 3σ` de amount por `(client, supplier)` nas últimas 6 conciliações. Se `|amount| > avg + 3σ` AND amostra ≥ 5 → flag `valor_outlier` (severity low). Pega cobranças anômalas (ex: tarifa mensal de R$ 30 vira R$ 500).

---

#### Entregáveis

1. **Novo módulo** `apps/api/app/modules/reconciliations/qualification/` com `semantic.py`, `historical.py`, `outliers.py` + `service.py` que orquestra as 3 camadas em ordem.
2. **4 novos tipos de anomalia** no seed (S15): `qualificacao_suspeita`, `qualificacao_incoerente`, `padrao_quebrado`, `valor_outlier`.
3. **Nova etapa no pipeline** (`job.py`): roda após `match()`, antes de `update_session_after_matching`. Em uma única transação extra (após a do matching) — falha não derruba a sessão, só não registra qualificação.
4. **Feature flag** `QUALIFICATION_ENABLED` (default true; permite desligar se Anthropic ficar fora).
5. **Excel — aba 2 (Movimentação)**: nova coluna **"Análise"** entre "Categoria Omie" e "Situação", com ícone (✅ / ⚠️ / ❌) baseado no maior severity de anomalia da linha.
6. **Excel — aba 1 (Resumo)**: novo bloco "Qualificação" com 5 contadores (coerentes, suspeitas, incoerentes, outliers, padrão quebrado).
7. **Excel — aba 5 (Anomalias)**: novas linhas dos 4 tipos novos (sem mudança no código — o loop já é genérico por anomaly_type).
8. **Endpoint de override** `POST /api/v1/reconciliations/.../anomalies/{id}/resolve` (verificar se já existe do S11; estender se sim). Analista pode marcar qualquer qualificação como "ok manualmente" com nota — persistida em `resolution_note_encrypted` (CLAUDE.md §4).
9. **Frontend** (FRONT 11.2 — task separada): chip de status por linha na aba Movimentação da revisão + filtro "Mostrar só com qualificação suspeita" + modal de override.
10. **Logging de custo Claude**: token usage por sessão estruturado no log (`structlog`), pra observabilidade da S17.

---

#### DoD

- [ ] Testes unitários (≥ 1 por camada: semantic com Claude mockado via `respx`, historical/outliers contra DB real com fixtures).
- [ ] Teste de integração end-to-end: sessão Sicredi Mar/2026 como fixture (já temos dados reais no DB), 13 pares conciliados, asserts no número de anomalias geradas.
- [ ] Feature flag testada em ambos os estados (`true` gera anomalias, `false` não toca o pipeline).
- [ ] Cost report: token usage logado e dentro do orçamento estimado.
- [ ] CI verde.
- [ ] Excel da sessão Sicredi Mar/2026 reprocessada mostra os 4 novos contadores + coluna Análise.

---

#### Fora de escopo (Fase 2 — pós-MVP)

- **Dashboard de tendências**: gráfico mês-a-mês de categorias mais frequentes por cliente.
- **Regras customizáveis por cliente**: analista cria mapeamento manual (_"sempre que descrição contém X, deve ser categoria Y"_).
- **Comparativo entre clientes** com mesmo setor (benchmark).
- **Re-análise sob demanda**: analista pode disparar nova análise de qualificação numa sessão já fechada.

Anotar essas extensões como issues técnicas no backlog após o release do MVP. Reusam a infraestrutura desta sessão; não exigem refatoração.

---

## 12. Riscos e Mitigações

| Risco                                         | Impacto                     | Mitigação                                                                                                                                                                                |
| --------------------------------------------- | --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Omie API instável/fora**                    | Alto                        | Retry com backoff, timeout rígido 15 s, status='error' com mensagem, runbook de "Omie fora".                                                                                             |
| **IA (Claude) erra no parsing**               | Alto                        | Gate humano obrigatório (preview 5 linhas). Validação pós-IA (saldo bate). Prompt com exemplos canônicos.                                                                                |
| **Arquivo malformado/gigante**                | Médio                       | Extensão + magic bytes + tamanho max 20 MB server-side. Timeout 60 s hard.                                                                                                               |
| **Custo Claude explode**                      | Médio                       | Prompt caching (90 % de desconto), Sonnet por padrão, pré-processamento PDF→texto, budget alert na Anthropic Console.                                                                    |
| **Vazamento de credencial Omie**              | Crítico                     | AES-256-GCM, chave só em env, nunca em log, rotação via script, Sentry filtra chaves sensíveis.                                                                                          |
| **Double-allocation no matcher**              | Crítico (dados incorretos)  | Set de IDs consumidos, testes unitários exaustivos, assertion final (`len(used) == sum(conciliated)`).                                                                                   |
| **Manager acessa cliente de outro**           | Crítico (confidencialidade) | RBAC verificado em toda rota, testes negativos obrigatórios em S6.                                                                                                                       |
| **Cache L2 cresce sem limites**               | Médio                       | TTL 2 h + limpeza periódica em in-memory; quando migrar para Redis, usar `maxmemory-policy allkeys-lru`.                                                                                 |
| **JWT comprometido**                          | Alto                        | Expiração curta (1 h), `active` verificado a cada request, rotação de `JWT_SECRET` suportada via `kid` no header.                                                                        |
| **Prompt injection via descrição do arquivo** | Médio                       | Descrições sanitizadas antes de enviar ao Claude no worker de detecção de anomalias (Fase 2). No parsing inicial, o arquivo é input do usuário esperado; delimitadores claros no prompt. |

---

## 13. Pontos em Aberto

**Decisões formalizadas em 24/04/2026:**

- [x] Framework Python → **FastAPI**
- [x] Job runner → **ARQ**
- [x] PM Python → **uv** · PM Frontend → **pnpm**
- [x] Estrutura → **monorepo simples** (`apps/api` + `apps/web` + `packages/shared-types`)

**Ainda dependem de validação com stakeholder antes das sessões correspondentes:**

- [ ] **Paginação do endpoint `ListarExtrato`** — documentação Omie incompleta; validar com Galhardo. _(S5)_
- [ ] **`ListarContasPagar.filtrar_por_status`** aceita múltiplos valores? _(S5)_
- [ ] **Saldo do Omie:** qual endpoint expõe saldo no 1º dia do mês como fallback de `balance_start`? _(S10)_
- [ ] ⚠️ **CRÍTICO — Credenciais Omie sandbox.** Pedro confirmou 25/04/2026 que ainda não tem. S5–S15 são implementadas com `respx` mockando respostas baseadas na doc oficial. **Lembrá-lo antes da S18 (deploy)** para obter as credenciais e validar o fluxo end-to-end contra a API real. _(S5–S18)_
- [ ] **Chave Anthropic** com budget configurado — responsabilidade de quem? _(S9)_
- [ ] **Storage de backups** — S3, GCS, cold storage local? Retenção desejada além dos 30 dias mínimos? _(S16)_
- [ ] **Plataforma de deploy (staging + prod)** — 3 candidatos pré-analisados no **Anexo S18.A** do plano: (A) Vercel + Fly + Neon + Upstash, (B) Render full-stack, (C) AWS sa-east-1. Decisão pendente de alinhamento com Hologram. Recomendação preliminar: Opção A. _(S18)_
- [ ] **Política de senhas** — rotação periódica, complexidade? (doc não define) _(S4)_
- [ ] **S19 entra no MVP ou vira release pós-deploy?** Pedido novo de sócio da Hologram (qualificação inteligente de lançamentos). Impacta o cronograma do S18. _(S19)_

---

## 14. Roadmap Macro

> Premissa: time de 2 – 3 devs full-stack sênior + 1 dev focado em integrações/IA.

| Fase                     | Sessões            | Duração estimada | Entregável                              |
| ------------------------ | ------------------ | ---------------- | --------------------------------------- |
| **Fundação**             | S0, S1, S2         | 1,5 semana       | Esqueleto pronto, CI verde.             |
| **Auth + Cadastros**     | S3, S4             | 1 semana         | Login, gestão de usuários.              |
| **Omie + Clientes**      | S5, S6, S7         | 2 semanas        | CRUD clientes + detalhe.                |
| **Pipeline conciliação** | S8, S9, S10        | 3 semanas        | Upload → parsing → matching end-to-end. |
| **Revisão + Export**     | S11, S12, S13, S14 | 3 semanas        | Tela de revisão completa + Excel.       |
| **Admin + Hardening**    | S15, S16, S17      | 1,5 semana       | Tipos de anomalia + segurança + obs.    |
| **E2E + Deploy**         | S18                | 1 semana         | Produção com runbooks.                  |
| **Qualificação**         | S19                | ~1 semana        | Análise semântica IA (ver §13).         |

**Total MVP base: ~13 semanas.** S19 adiciona ~1 semana caso entre no MVP; caso contrário, vira release 1.1 pós-deploy.

---

_Documento vivo — atualizar ao final de cada sessão com decisões tomadas, deltas em relação ao plano e links para PRs._
