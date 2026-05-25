# Avaliação Geral — Sistema de Auditoria de Lançamentos

**Auditor:** Claude Opus 4.7 (Anthropic), guiado pelo prompt de auditoria do Pedro.
**Data:** 2026-05-19.
**Escopo:** S0 → S11 (revisão + fixes) + S16 parcial. S12/S13 (frontend de revisão) auditados como parte do back-front. S14/S15/S17/S18 são roadmap não auditável.
**Branch / HEAD:** `main` @ `144be82` ("fix(magic-bytes): rejeitar binário pseudoaleatório na heurística csv").
**Método:** leitura de ~30 arquivos críticos do back + ~10 do front, 3 sub-agentes em paralelo cobrindo módulos não centrais, varredura por padrões inseguros (`eval/exec/shell=True/pickle/SQL cru`), execução de `ruff`/`mypy`/`pytest` (todos verdes), validação de regras invioláveis do CLAUDE.md.

> ⚠️ **Limitação metodológica:** `pip-audit` travou o sistema durante a auditoria; está bloqueado em `permissions.deny` global. **Dependências externas NÃO foram escaneadas para CVEs**. Pedro deve rodar manualmente fora do Claude (`uv run --with pip-audit pip-audit` num terminal próprio) ou via CI antes do deploy. Mesma observação vale para `npm audit`. Bandit/radon também não rodaram (falha de TLS no `uv pip install`); padrões equivalentes foram cobertos por grep manual.

> ⚠️ **Sub-agentes podem afirmar achados que não existem.** Validei manualmente os achados mais relevantes (paginação, race em "Trocar Omie", anomaly unique, `cache_control` ausente, `period_start` nullable, CASCADE em assignments). Os que dependem de leitura curta e não validei manualmente estão sinalizados com **(não validado manualmente)**.

---

## Resumo executivo

| Dimensão                  | Nota (1–10) | Comentário                                                                                                                                                                                                        |
| ------------------------- | :---------: | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Segurança                 |   **8.5**   | Crypto, RBAC, redação de logs e cookies HttpOnly muito sólidos. Falta `Secure=True` em prod garantido por config, falta CSRF defense-in-depth para SameSite=lax, e timing-attack pequeno no login.                |
| Arquitetura               |    **9**    | Modular, async puro, DI por `Depends`, sem singletons globais escondidos. Funções puras para domínio (matcher, crypto, search_index). Cache hierárquico L1/L2 bem isolado por client_id.                          |
| Qualidade do código       |    **9**    | `ruff` + `mypy strict` 100% verdes em 83 arquivos. Comentários explicam _por quê_, não _o quê_. Docstrings em todos os módulos públicos. Zero `eval`/`exec`/`shell=True`/SQL cru.                                 |
| Testes                    |   **7.5**   | 384 testes passando em 130s. Backend tem unit + integration com testcontainers. Frontend tem stack vitest+playwright instalada, mas **só 1 arquivo de teste no front (`*.test.tsx`)** — gap relevante pré-deploy. |
| Performance               |   **6.5**   | Matching O(n·m) ok pro volume previsto. **Paginação Python em `list_file_entries` carrega todas as linhas da sessão** — gargalo em sessões grandes (2k+ linhas). Decryption sem cache de request.                 |
| UX / Acessibilidade       |   **8.5**   | Loading/error/empty consistentes, ARIA em ícones, react-hook-form+zod em todos os formulários, badges com contraste WCAG AA. Sem virtualização (não necessário pra page_size=50).                                 |
| Manutenibilidade          |    **9**    | CLAUDE.md vivo, decisões registradas em commits ("Why" no diff), módulos seguem o padrão `routes/service/repository/schemas`.                                                                                     |
| DX                        |   **8.5**   | Scripts pnpm na raiz, Makefile, comandos óbvios. README claro. Logs estruturados com console pretty em dev.                                                                                                       |
| Conformidade funcional    |    **8**    | Backlog coberto até S11. Botão "Exportar" placeholder até S14 (esperado). Anomalia "sem nenhuma linha vinculada" é flexibilização consciente do Doc §14.5 — registrada nos comentários.                           |
| Observabilidade (pré-S17) |    **5**    | structlog + redactor + correlation_id propagado. Sem `/metrics`, sem Sentry conectado, sem painel de jobs ARQ. Esperado da S17.                                                                                   |
| **Média geral**           |  **7.95**   | Acima da média para um MVP solo+IA; veredicto detalhado abaixo.                                                                                                                                                   |

### Top 5 achados críticos (P0)

1. `P0-001` — **Cookie `Secure=False` por default em `Settings`** ([apps/api/app/core/config.py:89](apps/api/app/core/config.py#L89)). Se o deploy esquecer de setar `COOKIE_SECURE=true`, JWT vaza em redes não-TLS. **Crítico em prod**.
2. `P0-002` — **CSP / HSTS / X-Frame-Options / X-Content-Type-Options ausentes na API e no front** ([apps/api/app/main.py:182](apps/api/app/main.py#L182), front sem `next.config.js` de headers). Em prod, exposição a clickjacking, MIME sniffing, downgrade attacks.
3. `P0-003` — **Timing oracle no login** ([apps/api/app/modules/auth/service.py:50-68](apps/api/app/modules/auth/service.py#L50-L68)). Quando o email não existe, retorno é instantâneo; quando existe, roda bcrypt (cost 12 ≈ 200ms). Atacante enumera emails válidos por timing diff.
4. `P0-004` — **Rate limit ausente em `/parse` (consome Anthropic = $$) e em endpoints autenticados gerais** ([apps/api/app/modules/reconciliations/routes.py:157](apps/api/app/modules/reconciliations/routes.py#L157), [apps/api/app/core/rate_limit.py:14](apps/api/app/core/rate_limit.py#L14)). Hoje só `/login` tem rate limit. Um usuário malicioso ou bug no front pode estourar budget da Anthropic em minutos.
5. `P0-005` — **TrustedHost middleware ausente** ([apps/api/app/main.py:181-188](apps/api/app/main.py#L181-L188)). Sem `TrustedHostMiddleware`, FastAPI aceita qualquer `Host:` header — abre porta para host header injection (cache poisoning, password reset URL spoofing) quando estiver atrás de um proxy.

### Top 10 quick wins (alto impacto + baixo esforço)

| #   | Achado                                                                                      | Esforço |
| --- | ------------------------------------------------------------------------------------------- | ------- |
| 1   | Forçar `COOKIE_SECURE=True` quando `ENVIRONMENT=production` no validator                    | ⚡      |
| 2   | Adicionar `TrustedHostMiddleware` + `SecurityHeadersMiddleware` (CSP/HSTS/XFO/XCTO)         | ⚡      |
| 3   | Aplicar `@limiter.limit("10/minute")` em `/parse`; `60/minute` em mutations autenticadas    | ⚡      |
| 4   | `bcrypt.checkpw` com hash dummy se user inexistente (mata timing oracle)                    | ⚡      |
| 5   | Adicionar UNIQUE `(session_id, file_entry_id, omie_entry_id, anomaly_type_id)` em anomalies | ⚙️      |
| 6   | `ResolveAnomalyRequest` validar `resolution_note ≥ 10` no Pydantic (não só no service)      | ⚡      |
| 7   | Mover paginação de `list_file_entries_all` para SQL LIMIT/OFFSET                            | ⚙️      |
| 8   | `cache_control: ephemeral` no system prompt + tool schema (custo Anthropic)                 | ⚡      |
| 9   | `on_job_timeout` no ARQ que marca sessão como `error` (sessões zumbi)                       | ⚙️      |
| 10  | Conftest forçar `MOCK_PARSE=false` para o teste `test_admin_can_parse_any_client`           | ⚡      |

### Veredicto pré-deploy

**O sistema NÃO está pronto pra produção, mas falta pouco.** O coração (auth, crypto, matching, RBAC, orquestração ARQ) é excelente para um MVP solo+IA — mais robusto que muito código de equipe sênior. Os 5 P0 são todos **defaults inseguros que viram problema em prod**, não falhas de design — todos têm fix < 1h.

Antes de subir o staging do S18:

- Resolver os 5 P0 acima (~1 dia).
- Rodar `pip-audit` / `pnpm audit` fora do Claude (não foi possível durante esta auditoria).
- Implementar S17 (observabilidade) **antes** de deploy. Sem Sentry e métricas, qualquer incidente em prod fica cego — o produto manipula dado financeiro, isso é inegociável.
- Aceitar conscientemente os P1 que requerem trabalho maior (paginação SQL de file_entries, cleanup de sessões zumbi, decrypt cache) — eles não bloqueiam deploy, mas precisam virar tickets explícitos.

Coisas que **estão certas e não devem ser mexidas**:

- AES-256-GCM com IV-por-campo e tag embutida.
- Cookie HttpOnly + refresh interceptor deduplicado no front.
- Matching guloso determinístico.
- Cache L1+L2 com keys segregadas por `client_id`.
- Manager-fora-da-carteira retorna 404 consistentemente em todos os 10 endpoints de review (`_load_session_for_rbac` centralizou isso).
- bcrypt direto, sem passlib.
- Pydantic strict-in / lenient-out.

---

## Achados detalhados

### 🔴 Críticos (P0)

#### P0-001: Cookie `Secure=False` por default em Settings

- **Onde:** [apps/api/app/core/config.py:89](apps/api/app/core/config.py#L89)
- **Descrição:** `COOKIE_SECURE: bool = False`. Em produção, se o deploy esquecer a env var, cookies de access + refresh saem sem flag `Secure`, o que permite vazamento se houver qualquer comunicação não-TLS na cadeia (proxy mal configurado, redirect HTTP).
- **Impacto:** JWT exposto em redes não-TLS = roubo de sessão completa.
- **Reprodução:** subir API em prod com `COOKIE_SECURE` não setado → `curl` em `http://...` → resposta `Set-Cookie: access_token=...; HttpOnly; SameSite=Lax` (sem `Secure`).
- **Fix sugerido:** adicionar validator que **força `True` quando `ENVIRONMENT=production` ou `staging`**:
  ```python
  @model_validator(mode="after")
  def _enforce_secure_cookie_in_prod(self) -> Settings:
      if self.ENVIRONMENT in (Environment.PRODUCTION, Environment.STAGING) and not self.COOKIE_SECURE:
          raise ValueError("COOKIE_SECURE deve ser True em production/staging")
      return self
  ```
- **Effort:** ⚡
- **Referências:** CLAUDE.md §3.4 (cookie HttpOnly + Secure), OWASP A02:2021.

#### P0-002: Headers de segurança ausentes (CSP / HSTS / XFO / XCTO / Referrer-Policy)

- **Onde:** [apps/api/app/main.py:181-188](apps/api/app/main.py#L181-L188) (back) e ausência de `headers()` em `next.config.js`/`next.config.mjs` no front.
- **Descrição:** A API só tem `CORSMiddleware` + `CorrelationIdMiddleware`. Não há `SecurityHeadersMiddleware`. O front Next 14 também não declara `async headers()` no config — confirme. PLANO §5.1 #12 exige CSP restritivo, HSTS, X-Content-Type-Options, X-Frame-Options DENY, Referrer-Policy: same-origin.
- **Impacto:** clickjacking (XFO ausente), MIME sniffing (XCTO ausente), downgrade attack (HSTS ausente), exposição de Referer (Referrer-Policy ausente). Em prod sem proxy próprio injetando, esses ficam por conta da aplicação.
- **Fix sugerido:**
  - Back: `SecurityHeadersMiddleware` simples adicionado em `main.create_app()`:
    ```python
    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response
    ```
  - Front: definir `headers()` em `next.config.mjs` com `Content-Security-Policy` restrito (script-src 'self', img-src 'self', connect-src 'self' + BACKEND_URL).
- **Effort:** ⚡
- **Referências:** PLANO §5.1 #12, OWASP A05:2021.

#### P0-003: Timing oracle no login permite enumeração de emails

- **Onde:** [apps/api/app/modules/auth/service.py:50-68](apps/api/app/modules/auth/service.py#L50-L68)
- **Descrição:** Quando `user is None` (email não existe), o `login()` retorna `UnauthorizedError` imediatamente, sem rodar bcrypt. Quando o user existe, `verify_password` roda bcrypt (cost=12 ≈ 200ms). A diferença é mensurável por qualquer atacante e enumera emails válidos.
- **Impacto:** atacante consegue lista de emails válidos do sistema; combinado com credentials stuffing externo, eleva risco de takeover.
- **Reprodução:** medir `t = time` em `/api/v1/auth/login` com email aleatório vs email conhecido. Diferença consistente ~150-200ms.
- **Fix sugerido:** mesmo quando `user is None`, rodar `verify_password` contra um hash dummy fixo (constante de módulo) para equalizar o tempo. Atualmente o rate limit do `/login` (5/5min/IP) mitiga em parte, mas não impede enumeração lenta:
  ```python
  _DUMMY_HASH = "$2b$12$" + "a" * 53  # ou um hash real pré-computado de senha aleatória
  if user is None:
      verify_password("dummy", _DUMMY_HASH)  # come tempo de bcrypt
      raise UnauthorizedError(..., user_message=GENERIC_LOGIN_ERROR)
  ```
- **Effort:** ⚡
- **Referências:** CLAUDE.md §3.9, OWASP A07:2021.

#### P0-004: Rate limit ausente em /parse e em mutations gerais

- **Onde:** [apps/api/app/modules/reconciliations/routes.py:157](apps/api/app/modules/reconciliations/routes.py#L157) (`@router.post("/parse")` sem `@limiter.limit`), [apps/api/app/core/rate_limit.py:14](apps/api/app/core/rate_limit.py#L14) (TODO S16 explícito).
- **Descrição:** PLANO §5.1 #11 prescreve `120 req/min/user` em autenticados gerais e `10 req/min/user` em parsing/export. Hoje só `/login` tem rate limit. `/parse` consome Anthropic API ($$$). Um usuário comprometido ou bug no front pode estourar budget mensal em minutos.
- **Impacto:**
  - Financeiro: budget Anthropic explode.
  - Disponibilidade: matching usa httpx pool — não há cap por usuário.
  - DoS interno: 1 user pode prender 4 workers ARQ (max_jobs=4) com submits encadeados.
- **Fix sugerido:**
  - Adicionar `@limiter.limit("10/minute")` em `/parse` e `POST /reconciliations` (keyed por user_id, não IP, pra evitar 1 escritório atrás de NAT estourar pra todos).
  - Adicionar `@limiter.limit("120/minute")` como default em mutations.
  - Slowapi com `RATELIMIT_STORAGE_URI=redis://...` em multi-instance (já documentado no docstring).
- **Effort:** ⚙️ (precisa testar e definir keying por user).
- **Referências:** PLANO §5.1 #11.

#### P0-005: TrustedHostMiddleware ausente

- **Onde:** [apps/api/app/main.py:181-188](apps/api/app/main.py#L181-L188)
- **Descrição:** Sem `TrustedHostMiddleware`, qualquer `Host:` header é aceito. Em prod atrás de proxy reverso, isso permite host header injection (cache poisoning, password reset URL spoofing, etc.). Não é exploit imediato mas é OWASP A05.
- **Impacto:** atacante força resposta com `Location: https://attacker.com/reset?token=...` quando alguma rota constrói URL com `request.url.hostname`.
- **Fix sugerido:**

  ```python
  from starlette.middleware.trustedhost import TrustedHostMiddleware
  app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts_list)
  ```

  - adicionar `ALLOWED_HOSTS` em `Settings` (CSV, default `"localhost,127.0.0.1"` em dev).

- **Effort:** ⚡
- **Referências:** OWASP A05:2021, [Starlette docs](https://www.starlette.io/middleware/#trustedhostmiddleware).

---

### 🟠 Altos (P1)

#### P1-001: Paginação Python em `list_file_entries` carrega todas as linhas da sessão

- **Onde:** [apps/api/app/modules/reconciliations/review/service.py:155-164](apps/api/app/modules/reconciliations/review/service.py#L155-L164)
- **Descrição:** `service.list_file_entries` chama `repo.list_file_entries_all` (todas as linhas da sessão), faz `total = len(rows)`, então slice `rows[start:start+page_size]`. O comentário (linhas 130-134) justifica como "decrypt da página é barato"; **mas o problema não é decrypt — é o SELECT sem LIMIT e o transporte de N linhas com `description_encrypted` (TEXT) e IV até o app**. Sessões com 2000 linhas ≈ 4-8 MB transferidos por request, sempre.
- **Impacto:** memória do worker uvicorn cresce com volume da sessão; latência da Tela de Revisão fica O(N) em vez de O(page_size). PLANO §6.2 estima 100-2000 linhas/arquivo — no teto desse range já dói.
- **Reprodução:** seed uma sessão com 5000 file_entries, chamar `GET /reconciliations/{id}/file-entries?page=1&page_size=20` → ver latência e RSS do worker.
- **Fix sugerido:** mover paginação para SQL no `list_file_entries_all` (LIMIT/OFFSET após o WHERE/ORDER BY que já existe). Como `description_search_hmac` é uma coluna SQL e os outros filtros também, paginar em SQL é trivial. O slice atual passa a ser um `LIMIT page_size OFFSET (page-1)*page_size`. Decrypt continua na página final.
- **Effort:** ⚙️
- **Referências:** PLANO §6.2.

#### P1-002: Sem UNIQUE em anomalies — duplicáveis em concorrência

- **Onde:** [apps/api/app/db/models/reconciliation_anomaly.py:50-61](apps/api/app/db/models/reconciliation_anomaly.py#L50-L61), [apps/api/app/modules/reconciliations/review/service.py:535+](apps/api/app/modules/reconciliations/review/service.py)
- **Descrição:** `POST /api/v1/reconciliations/{id}/anomalies` cria a anomalia sem checar duplicação. Duas requests concorrentes do mesmo analista (ou retry do front em rede flaky) podem inserir 2 linhas idênticas: mesmo `(session_id, file_entry_id, omie_entry_id, anomaly_type_id)`. Front conta `anomaly_count` duplicado, lista mostra duas.
- **Impacto:** dados sujos em produção, contadores divergem, relatório Excel S14 acusa duplicidade. Não é catastrófico mas é visível.
- **Fix sugerido:** UNIQUE INDEX em `(session_id, file_entry_id, omie_entry_id, anomaly_type_id)`. Como `file_entry_id` e `omie_entry_id` são nullable, usar índice **único parcial** ou normalizar NULLs com `COALESCE` (Postgres trata NULL diferente em UNIQUE). Approach mais limpo:
  ```python
  Index(
      "ix_recon_anomalies_unique_link",
      "session_id",
      "anomaly_type_id",
      func.coalesce("file_entry_id", text("'00000000-0000-0000-0000-000000000000'::uuid")),
      func.coalesce("omie_entry_id", text("'00000000-0000-0000-0000-000000000000'::uuid")),
      unique=True,
  )
  ```
  Combinar com captura de `IntegrityError` no service e devolver `ConflictError` 409 em PT-BR.
- **Effort:** ⚙️
- **Referências:** confirmado em [apps/api/app/modules/reconciliations/review/schemas.py:228-232](apps/api/app/modules/reconciliations/review/schemas.py#L228-L232) que o XOR é deliberadamente flexibilizado.

#### P1-003: Worker zumbi — sessão fica em `processing` indefinidamente se ARQ morre/timeout silencia

- **Onde:** [apps/api/app/workers/arq_worker.py:88-91](apps/api/app/workers/arq_worker.py#L88-L91), [apps/api/app/modules/reconciliations/processing/job.py:101-108](apps/api/app/modules/reconciliations/processing/job.py#L101-L108)
- **Descrição:** `WorkerSettings.job_timeout=300s` e `max_tries=1`. Se o worker for kill-9-ado no meio do job ou se o timeout do ARQ disparar, **nenhum hook marca a sessão como `error`**. `_safe_mark_error` só é chamado dentro do try/except do `run_reconciliation_processing` — não é chamado pelo ARQ quando ele faz o timeout externo. A sessão fica `status='processing'` para sempre; o front polla a cada 3s indefinidamente.
- **Impacto:** sessões zumbi acumulam; user vê "ainda processando" sem ter quem mexer. Suporte tem que ir no DB rodar UPDATE manual.
- **Fix sugerido:**
  - Adicionar `on_job_failure` hook no `WorkerSettings` que marca a sessão como `error`.
  - Alternativa: cleanup job rodando a cada 10min que olha `WHERE status='processing' AND created_at < NOW() - INTERVAL '15 min'` e marca como erro.
  - Documentar como runbook em `Docs/runbook.md` (será criado em S18).
- **Effort:** ⚙️
- **Referências:** [arq_worker.py:8-22 comentário admite max_tries=1](apps/api/app/workers/arq_worker.py#L8-L22).

#### P1-004: `XOR` de anomalia aceita "nenhum vínculo" — produz dados órfãos

- **Onde:** [apps/api/app/modules/reconciliations/review/schemas.py:226-238](apps/api/app/modules/reconciliations/review/schemas.py#L226-L238)
- **Descrição:** O comentário admite explicitamente: "Aceita 'nenhum'? Doc §14.5 diz que é sempre vinculada a uma linha — mas pra evitar quebra em demo flexibilizamos". Em prod, isso permite anomalias sem âncora — listagem mostra "anomalia desconhecida" e o Excel S14 vai precisar de fallback.
- **Impacto:** dados órfãos em produção; relatório confuso.
- **Fix sugerido:** se a flexibilização era só para a demo, **remover antes de production**. Voltar para XOR estrito:
  ```python
  if self.file_entry_id is None and self.omie_entry_id is None:
      raise ValueError("Anomalia precisa estar vinculada a uma file_entry OU a uma omie_entry.")
  ```
  Decidir com o time se isso quebra alguma feature já gravada.
- **Effort:** ⚡
- **Referências:** Doc §14.5.

#### P1-005: Decryption sem cache de request — mesma descrição decifrada N vezes

- **Onde:** [apps/api/app/modules/reconciliations/review/service.py:167-182](apps/api/app/modules/reconciliations/review/service.py#L167-L182)
- **Descrição:** Cada linha tem seu próprio `description_iv` (correto — nunca reusar IV). Mas no contexto da listagem, a mesma descrição "Pagamento fornecedor X" cifrada com IVs diferentes em 30 linhas é decifrada 30 vezes. Cada decrypt AES-GCM é ~50µs — para 50 linhas, 2.5ms. Não é catastrófico, mas escala mal.
- **Impacto:** CPU desperdiçada; em sessões grandes (P1-001 piora junto) latência soma.
- **Fix sugerido:** request-scoped LRU `dict[(ct_hex, iv_hex), str]` no `ReviewService`. Hit pula AES-GCM; miss faz e armazena. Vida do cache = lifetime do service (1 request).
- **Effort:** ⚡
- **Referências:** [crypto.py:64-82](apps/api/app/core/crypto.py#L64-L82).

#### P1-006: ListarExtrato sem paginação — truncamento silencioso se Omie passar a paginar

- **Onde:** [apps/api/app/integrations/omie/client.py:375-405](apps/api/app/integrations/omie/client.py#L375-L405)
- **Descrição:** TODO comentado: "a documentação Omie não especifica se este endpoint pagina. Por ora assumimos que retorna tudo numa chamada". Se o Omie começar a paginar e o sistema continuar a chamar sem `pagina/registros_por_pagina`, recebe só a primeira página — matching fica incompleto e **nenhum erro é gerado**.
- **Impacto:** auditoria errada silenciosa — analista olha o resultado e confia, mas matching foi feito sobre dataset parcial. Em conciliação, isso é P0 do ponto de vista de produto.
- **Fix sugerido:**
  - Adicionar paginação defensiva via `_paginate()` (já existe). Mesmo se Omie hoje retorna tudo numa chamada, paginar com page_size grande é compatível.
  - Logar `WARNING` quando uma única chamada retornar > N itens (sinal de que pode ter mais).
  - Esclarecer com o Galhardo (TODO já mapeado em CLAUDE.md §10).
- **Effort:** ⚙️
- **Referências:** CLAUDE.md §10 "Pontos em Aberto", Doc Omie.

#### P1-007: Test `test_admin_can_parse_any_client` quebra com `MOCK_PARSE=true` no `.env` local

- **Onde:** Dívida técnica conhecida (CLAUDE.md "Dívidas técnicas já mapeadas" #1) — não encontrei `conftest.py` que neutralize.
- **Descrição:** `.env` local com `MOCK_PARSE=true` faz o mock retornar sempre Itaú-fictício, e o teste espera Sicredi. Falha quando rodado por outro dev que tenha `MOCK_PARSE=true` setado para demo.
- **Impacto:** teste flaky por config local; novo dev vai bater nisso.
- **Fix sugerido:**
  ```python
  # conftest.py
  @pytest.fixture(autouse=True)
  def _force_real_parse(monkeypatch):
      monkeypatch.setenv("MOCK_PARSE", "false")
      get_settings.cache_clear()
  ```
- **Effort:** ⚡
- **Referências:** CLAUDE.md "Dívidas técnicas já mapeadas" #1.

#### P1-008: `cache_control: ephemeral` ausente nos blocos system/tool da Anthropic — custo desnecessário

- **Onde:** [apps/api/app/integrations/anthropic/client.py:226-233](apps/api/app/integrations/anthropic/client.py#L226-L233) (`_build_system_blocks`), [apps/api/app/integrations/anthropic/tools.py:9-11](apps/api/app/integrations/anthropic/tools.py#L9-L11) (TODO admitido).
- **Descrição:** O SYSTEM_PROMPT e o tool schema (`EXTRACT_MOVEMENTS_TOOL`) são imutáveis. Sem `cache_control: ephemeral`, cada chamada à Anthropic re-processa todo o prompt cacheável — perde-se ~90% do desconto de prompt caching. PLANO §6.2 cita isso como mitigação obrigatória.
- **Impacto:** custo Anthropic multiplicado por ~10x em escala (5000 conciliações/mês × custo total do prompt em vez de só os tokens variáveis).
- **Fix sugerido:** adicionar `"cache_control": {"type": "ephemeral"}` nos blocos longos:
  ```python
  return [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
  ```
- **Effort:** ⚡
- **Referências:** PLANO §6.2 #2, [Anthropic Prompt Caching docs](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching).

#### P1-009: Frontend tem stack de teste configurada mas só 1 arquivo de teste

- **Onde:** `apps/web/src/**/*.test.{ts,tsx}` — confirme contagem; o agente do front não relatou specs encontrados.
- **Descrição:** PLANO §8.2 prescreve vitest + RTL + playwright. As deps estão no `package.json` mas a auditoria não localizou arquivos de teste relevantes no front. Lógica não trivial (validation de zod, refresh interceptor deduplicado, anomalia XOR, parse preview) está sem cobertura automatizada.
- **Impacto:** regressões no front passam direto; refactor fica perigoso.
- **Fix sugerido:** mínimo viável pré-deploy:
  - Unit tests para `lib/api/client.ts` (refresh interceptor + unwrap de `data`).
  - Unit tests para schemas zod (`lib/validation/*`).
  - 1 E2E playwright cobrindo login → criar cliente fictício → nova conciliação → revisão → mock export.
- **Effort:** 🛠
- **Referências:** PLANO §8.2.

---

### 🟡 Médios (P2)

#### P2-001: Contadores da sessão não-atômicos em concorrência

- **Onde:** [apps/api/app/modules/reconciliations/review/repository.py:180-214](apps/api/app/modules/reconciliations/review/repository.py#L180-L214)
- **Descrição:** `recompute_file_entry_counters` faz `SELECT COUNT GROUP BY` + `UPDATE`. Dois PATCH file-entries concorrentes que mudam `situation` correm o risco de ler counts intermediários e o último UPDATE prevalecer. Convergência eventual: próximo PATCH conserta. Aceitável para o MVP (UX é correção em F5).
- **Impacto:** header de revisão mostra contador divergente por alguns segundos em uso simultâneo.
- **Fix sugerido (futuro):** trigger DB que mantém contadores ou UPDATE relativo (`SET conciliated_count = conciliated_count + 1`). Para o MVP, documentar "best-effort" no schema da response.
- **Effort:** ⚙️
- **Referências:** CLAUDE.md §11 "Escalabilidade considerada".

#### P2-002: `ResolveAnomalyRequest` valida `resolution_note ≥ 10` no service, não no schema

- **Onde:** [apps/api/app/modules/reconciliations/review/schemas.py:250-258](apps/api/app/modules/reconciliations/review/schemas.py#L250-L258)
- **Descrição:** O Pydantic só limita `max_length=2000`. A regra "se resolved=true, note ≥ 10 chars" roda no service. Inconsistência com o resto do projeto (Pydantic strict-in).
- **Impacto:** OpenAPI/docs não reflete a regra; clients gerados a partir do schema sem essa validação.
- **Fix sugerido:** `@model_validator(mode="after")` no schema com a regra. Mensagem técnica em inglês, `user_message` PT-BR pelo service quando precisar.
- **Effort:** ⚡

#### P2-003: Mensagem genérica em `RequestValidationError` perde detalhe

- **Onde:** [apps/api/app/main.py:133-147](apps/api/app/main.py#L133-L147)
- **Descrição:** Handler global retorna `userMessage: "Dados inválidos. Verifique os campos enviados."` sem expor _quais_ campos. Em desenvolvimento isso atrapalha; em produção é OK por segurança, mas o front fica sem nada actionable.
- **Impacto:** UX ruim em validação — usuário não sabe qual campo errou.
- **Fix sugerido:** incluir `errors` estruturado (`field`, `code`) no payload do erro 400 (Pydantic já oferece). Front pode mapear pra field-level errors no formulário.
- **Effort:** ⚙️
- **Referências:** PLANO §9.1.

#### P2-004: 3+ queries por GET de detail de sessão

- **Onde:** [apps/api/app/modules/reconciliations/routes.py:305-334](apps/api/app/modules/reconciliations/routes.py#L305-L334)
- **Descrição:** Fluxo: `get_detail_view` (1 SELECT) → `require_client_access` (1 SELECT client + 1 SELECT assignment se manager) → `service.get_session_detail` (mais queries dentro do service). N+1 não, mas 3-5 round trips em sequência onde 1-2 bastariam.
- **Impacto:** latência P95 da Tela de Revisão.
- **Fix sugerido:** `get_detail_view` com `selectinload(ReconciliationSession.client)` + reusar `client` no require_client_access. Refactor sem grande risco.
- **Effort:** ⚙️

#### P2-005: Logger redactor só cobre top-level keys, não nested dicts

- **Onde:** [apps/api/app/core/logging.py:58-73](apps/api/app/core/logging.py#L58-L73)
- **Descrição:** `_redact_sensitive` itera sobre `event_dict.keys()` no nível raiz. Se algum log fizer `log.info("X", body={"password": "secret"})`, o valor `secret` não é redatado porque a chave sensível é nested. Hoje o código real **não tem** isso (validei via grep de logger calls), mas é um problema latente.
- **Impacto:** primeiro dev a logar um `body` ou `payload` vaza credencial sem aviso.
- **Fix sugerido:** processor recursivo:
  ```python
  def _redact_recursive(obj):
      if isinstance(obj, dict):
          return {k: REDACTED_VALUE if _is_sensitive(k) else _redact_recursive(v) for k, v in obj.items()}
      if isinstance(obj, list):
          return [_redact_recursive(x) for x in obj]
      return obj
  ```
- **Effort:** ⚡

#### P2-006: AppError genérico no `create_reconciliation` quando enqueue falha

- **Onde:** [apps/api/app/modules/reconciliations/routes.py:244-254](apps/api/app/modules/reconciliations/routes.py#L244-L254)
- **Descrição:** `raise AppError(f"Falha ao enfileirar job ...")` — AppError base tem `code=INTERNAL_ERROR` e `status=500`. O comentário diz que isso causa rollback do `DbSessionDep`, **mas o `f"Falha ao enfileirar job para session_id={session_id}: {exc}"` interpola o `exc` no `message`**, que pode conter detalhes de conexão Redis. O `message` técnico não vai pra resposta, mas vai pro log — OK, mas é frágil.
- **Impacto:** baixo, mas o pattern é inconsistente com o resto do projeto (que usa exceções tipadas).
- **Fix sugerido:** criar `QueueUnavailableError(AppError)` com `code=INTERNAL_ERROR`, `user_message` em PT-BR.
- **Effort:** ⚡

#### P2-007: Cache L2 Redis sem cap configurável (TTLCache no L1 tem `maxsize=10_000`, no Redis nada)

- **Onde:** [apps/api/app/integrations/omie/lancamento_cache.py:65, 287-298](apps/api/app/integrations/omie/lancamento_cache.py#L65)
- **Descrição:** L1 tem `DEFAULT_L1_MAXSIZE=10_000` (bom). L2 só tem TTL (2h). Em uso intenso, Redis cresce sem bound até TTL expirar; em ambientes pequenos do staging, isso pode encher Redis.
- **Impacto:** Redis OOM em staging compartilhado.
- **Fix sugerido:** documentar em runbook que Redis precisa de `maxmemory-policy allkeys-lru` no `redis.conf` de prod.
- **Effort:** ⚡

#### P2-008: `period_start`/`period_end` nullable sem indicação ao usuário em sessões antigas

- **Onde:** [apps/api/app/db/models/reconciliation_session.py:88-89](apps/api/app/db/models/reconciliation_session.py#L88-L89)
- **Descrição:** A migration `4a2f9e8b1c3d` deixa colunas NULL em sessões pré-S11. O review service tem fallback para `[reference_month, last_day_of_month]`. Não há sinal no UI de que aquela sessão usa fallback.
- **Impacto:** baixo em prod (sessões pré-S11 só existem em dev). Vira P3 quando subir.
- **Fix sugerido:** quando virar prod, considerar backfill manual via script (`UPDATE reconciliation_sessions SET period_start = reference_month, period_end = (reference_month + INTERVAL '1 month - 1 day') WHERE period_start IS NULL`). Não bloqueante.
- **Effort:** ⚡

#### P2-009: Sem `/metrics` endpoint nem health do worker visível

- **Onde:** [apps/api/app/main.py:208-221](apps/api/app/main.py#L208-L221) tem `/health` e `/health/ready` (DB). Worker ARQ não expõe status.
- **Descrição:** Observabilidade do worker ainda não está em S17. Sem `/metrics` (Prometheus) nem status do ARQ, qualquer incidente em prod fica cego.
- **Fix sugerido:** S17 já está mapeada. Mínimo viável pré-deploy: endpoint `GET /api/v1/admin/worker-health` (admin-only) que olha o tamanho da fila no Redis.
- **Effort:** ⚙️

#### P2-010: `parse_service.py` latin-1 fallback com `errors="replace"` pode introduzir U+FFFD no prompt da Anthropic

- **Onde:** [apps/api/app/integrations/anthropic/client.py:280-281](apps/api/app/integrations/anthropic/client.py#L280-L281)
- **Descrição:** Se um CSV vier em encoding exótico (UTF-16, Windows-1252), o fallback latin-1 mapeia bytes 1:1, mas se houver bytes >127 que não decodificam em UTF-8, vão como caracteres latin-1 — para arquivos em PT-BR isso normalmente funciona; para arquivos com BOM UTF-16 vira lixo.
- **Impacto:** parse pode dar resultado errado; Claude tenta fazer sentido do lixo.
- **Fix sugerido:** detectar encoding com `chardet` ou `charset-normalizer` antes de decodificar. Não crítico mas é P2.
- **Effort:** ⚙️

---

### 🟢 Baixos (P3)

#### P3-001: BASE_URL fallback hardcoded `http://localhost:8000` no front

- [apps/web/src/lib/api/client.ts:16](apps/web/src/lib/api/client.ts#L16): `?? 'http://localhost:8000'`. Em prod, se `NEXT_PUBLIC_API_URL` não estiver no build, todas as chamadas vão pra localhost (provavelmente quebram, mas vazam o fallback no bundle). Fix: lançar erro no build se ausente em `NODE_ENV=production`.

#### P3-002: `_load_session_for_rbac` constrói `CurrentUser(email="", name="", ...)` ad-hoc

- [apps/api/app/modules/reconciliations/review/routes.py:96-102](apps/api/app/modules/reconciliations/review/routes.py#L96-L102): cria CurrentUser fake só pra reusar `require_client_access`. Funciona, mas é code smell. Refactor: extrair função `require_client_access_by_id(client_id, user_id, role, db)` que não precisa do objeto.

#### P3-003: Comentário "TODO S16" no `rate_limit.py` está parcialmente resolvido — atualizar

- [apps/api/app/core/rate_limit.py:13-19](apps/api/app/core/rate_limit.py#L13-L19): comentário menciona combinar IP+email. S16 fechou outros itens; este permanece. Aceitar e linkar para issue.

#### P3-004: `MockOmieClient` não usa o mesmo logger que o `OmieClient` real

- [apps/api/app/integrations/omie/mock_client.py:267](apps/api/app/integrations/omie/mock_client.py#L267): `log.warning("omie_mock_client_built")`. OK, mas mistura logs reais com mockados em produção se alguém deixar `FAKE_DEMO_OMIE_` em algum cliente real. Fix: validator em `Settings.is_production` que recusa booting com qualquer cliente com prefixo fake. (Provavelmente cobre por checagem de credencial no startup, mas vale documentar.)

#### P3-005: `processing-screen.tsx` simula steps por tempo, não por sub-status do back

- Roadmap conhecido (CLAUDE.md "Decisões já tomadas e CONSCIENTES"). Será reescrito quando S17 entregar SSE/eventos. Não é bug.

#### P3-006: `Test Connection` em `clients` não tem rate limit

- [apps/api/app/modules/clients/routes.py:121](apps/api/app/modules/clients/routes.py#L121): aceita app_key + app_secret no body e chama Omie. Sem rate limit, um manager pode pressionar Omie pra fazer enumeração de credentials (`429` do Omie após várias chamadas seria o sinal). Baixíssimo risco interno, mas vale rate limit `10/min` por user.

#### P3-007: `_safe_mark_error` é best-effort sem cleanup automatizado

- [apps/api/app/modules/reconciliations/processing/job.py:261-279](apps/api/app/modules/reconciliations/processing/job.py#L261-L279). Comentário admite o gap. Já listei como parte do P1-003.

#### P3-008: `clients/repository.py:108-109` faz count+select em sequência (poderia ser CTE single round-trip)

- Otimização menor; legibilidade pesa mais que latência aqui. Fica como nota.

#### P3-009: Frontend não declara `headers()` em `next.config.mjs` — duplicado do P0-002 mas no front.

#### P3-010: `Decimal` em `ExtractedStatement.transactions[].amount` — risco de float intermediário se IA emitir number

- A Pydantic v2 com schema `"type": "number"` da Anthropic recebe JSON number → float em Python → Decimal no schema (assume que o schema do Pydantic faz `Decimal(str(value))`). Confirme em [apps/api/app/integrations/anthropic/schemas.py]. Se for `Decimal(value)` direto sem `str()`, perde precisão. Baixo risco (raramente atinge a 3ª decimal), mas vale validar.

#### P3-011: Documentação de runbook ausente

- Está na S18, mas mesmo antes do deploy seria útil ter um `Docs/runbook.md` rascunhado com "o que fazer se sessão fica em processing", "como rotacionar OMIE_ENCRYPTION_KEY", etc. Já é roadmap reconhecido.

---

## Pontos fortes

1. **Crypto bem feita.** AES-256-GCM, IV novo por operação (12 bytes), tag embutida, decrypt opaco ("dado adulterado ou chave incorreta") — segue [crypto.py:114-115](apps/api/app/core/crypto.py#L114-L115) à risca. Blind index search HMAC separado de chave de criptografia ([config.py:77-83](apps/api/app/core/config.py#L77-L83)) — separação de domínios bem pensada.

2. **RBAC consistente e centralizado.** `require_client_access` em [dependencies.py:120-151](apps/api/app/core/dependencies.py#L120-L151); todos os 10 endpoints da review passam por `_load_session_for_rbac` ([review/routes.py:80-109](apps/api/app/modules/reconciliations/review/routes.py#L80-L109)) que converte 403 em 404 para manager-fora. Nenhuma rota vaza existência por código de status.

3. **Async puro.** Nenhum `time.sleep`, nenhum `requests.get` síncrono, tudo `httpx.AsyncClient` + `AsyncSession` + `AsyncRetrying`. Worker ARQ é async-first (mata o problema de `asyncio.run` que Celery teria).

4. **Type safety estrita.** `mypy --strict` clean em 83 arquivos. Praticamente sem `# type: ignore` (vi só 1 no `cast` do Anthropic SDK — justificado).

5. **Padrão de módulo respeitado.** `routes.py / service.py / repository.py / schemas.py` em todos os módulos. Sem mistura de SQL no service nem regras de negócio no repository.

6. **Logs estruturados com redação automática.** [logging.py:36-53](apps/api/app/core/logging.py#L36-L53). 14 substrings cobrem o universo de chaves sensíveis. Logs de Omie/Anthropic só carregam metadata segura (módulo, duração, status, contagem) — confirmei manualmente em [omie/client.py](apps/api/app/integrations/omie/client.py) e [anthropic/client.py](apps/api/app/integrations/anthropic/client.py).

7. **Comentários explicam _por quê_.** Cada decisão controversa tem um parágrafo: divergência intencional do schema da Doc em [client.py:5-15](apps/api/app/db/models/client.py#L5-L15), trade-off de paginação Python em [review/service.py:130-134](apps/api/app/modules/reconciliations/review/service.py#L130-L134), por que `_load_session_for_rbac` retorna 404 em [review/routes.py:87](apps/api/app/modules/reconciliations/review/routes.py#L87). Isso vale ouro pra quem chega depois.

8. **Idempotência declarada em DB.** `UNIQUE(client_id, omie_conta_id, reference_month, file_hash)` em sessions + índice único parcial em file_entries para "Trocar Omie" ([reconciliation_file_entry.py:64-70](apps/api/app/db/models/reconciliation_file_entry.py#L64-L70)). DB é a fonte da verdade da invariante — não apenas o app.

9. **Frontend disciplinado.** TanStack Query em 100% dos fetches, react-hook-form + zod em 100% dos forms, cookie HttpOnly via fetch wrapper com refresh deduplicado ([api/client.ts:66-88](apps/web/src/lib/api/client.ts#L66-L88)). Zero `useEffect+fetch`. Zero `dangerouslySetInnerHTML`. Zero token em localStorage.

10. **Matching determinístico bem desenhado.** [matcher.py](apps/api/app/modules/reconciliations/processing/matcher.py) é função pura (sem I/O, sem ORM), testa exaustivamente sem precisar de DB. Desempate em tupla `(days_diff, amount_diff, date)` é estável e auditável.

---

## Recomendações estratégicas

### Próximos 7 dias (antes de qualquer feature nova)

1. **Resolver os 5 P0** (~1 dia de dev): COOKIE_SECURE forçado em prod, headers de segurança, timing oracle, rate limit em `/parse` + mutations, TrustedHostMiddleware.
2. **Resolver P1-002 (UNIQUE em anomalies)** e **P1-004 (XOR estrito)** — dados sujos em prod nascem aqui.
3. **Resolver P1-008 (cache_control Anthropic)** — dinheiro literal sendo gasto à toa toda demo.
4. **Adicionar `conftest.py` neutralizando `MOCK_PARSE`** (P1-007).
5. **Rodar `pip-audit` e `pnpm audit`** num terminal fora do Claude e tratar qualquer CVE crítico — esta auditoria NÃO cobriu.

### Antes do deploy (S18)

1. **Implementar S17 inteira** (Sentry + structlog → Loki/Grafana + métricas básicas) — não é negociável para um sistema financeiro.
2. **Mover paginação de `list_file_entries` para SQL** (P1-001) — escalabilidade real depende disso.
3. **Hook de `on_job_failure` no ARQ** (P1-003) — sessões zumbi são onboarding ruim.
4. **Mínimo de testes E2E no front** (P1-009) — golden path login → revisão → export.
5. **Validar paginação do `ListarExtrato` com o Galhardo** (P1-006) — risco silencioso real.
6. **Rotacionar `ANTHROPIC_API_KEY` e `OMIE_ENCRYPTION_KEY`** para a versão business (dívida técnica mapeada em CLAUDE.md).
7. **Script de rotação de `OMIE_ENCRYPTION_KEY`** com re-criptografia em batch (dívida técnica #5).
8. **Runbook** (`Docs/runbook.md`): "sessão zumbi", "rotação de chave", "Redis OOM", "Omie auth failure massa".

### Pós-MVP (3–6 meses)

1. **Cache de decrypt por request** (P1-005) — quando volume passar de 50 sessões/dia ativas.
2. **Trigger DB para contadores** (P2-001) — só quando houver UX issue real.
3. **SSE/WebSocket para `processing-screen`** — substituir polling 3s (sub-status reais).
4. **Stemming/prefix matching no blind index** — `compute_search_hmac` hoje só casa tokens completos.
5. **`AnomalyType` admin CRUD** (S15) e **Excel export** (S14).
6. **Backup criptografado off-site** (PLANO §5.1 #14).
7. **Painel de monitoramento de Omie API** (latência, taxa de 5xx, faultstring frequência).

---

## Apêndice — Métricas concretas

| Métrica                                               |                   Valor |
| ----------------------------------------------------- | ----------------------: |
| Commits no `main`                                     |                      50 |
| Arquivos Python (apps/api/app)                        |                      83 |
| Arquivos TS/TSX (apps/web/src)                        |                      73 |
| Migrations Alembic                                    |                       5 |
| Testes backend passando                               |                     384 |
| Tempo de `pytest` (unit + integration)                |                    130s |
| `ruff check`                                          |                clean ✅ |
| `mypy --strict`                                       |  clean (83 arquivos) ✅ |
| Cobertura backend (`pytest-cov`)                      | NÃO MEDIDA (limitação)¹ |
| Cobertura frontend                                    |             NÃO MEDIDA¹ |
| Funções com complexidade > 10 (`radon`)               |             NÃO MEDIDA¹ |
| Dependências vulneráveis (`pip-audit` / `pnpm audit`) |            NÃO MEDIDAS¹ |
| TODOs/FIXMEs em `apps/api/app` + `apps/web/src`       |   ~10 (CLAUDE.md docs)² |
| Issues Bandit (HIGH/MEDIUM)                           |            NÃO MEDIDAS¹ |
| Bundle size do front                                  |             NÃO MEDIDO¹ |

¹ Limitação metodológica: `pip-audit` travou o sistema, `uv pip install` de bandit/radon/pytest-cov falhou por TLS/cert. Pedro precisa rodar manualmente fora do Claude.
² Estimativa visual; CLAUDE.md §7 proíbe `// TODO: fix later` no código — TODOs vivem em comentários explicativos linkando para sessões/pontos em aberto.

---

## Apêndice — Decisões trade-off reconhecidas (NÃO contestar)

Lista de decisões que o time já fez conscientemente, **avaliadas e mantidas**:

- **Stack:** FastAPI + ARQ + uv + pnpm + monorepo simples (CLAUDE.md §2). ✅
- **AES-GCM com IV por campo** (não 1 IV global como na doc original) — [client.py:5-15](apps/api/app/db/models/client.py#L5-L15). ✅
- **bcrypt direto sem passlib** (incompatibilidade bcrypt 5.x — `feedback_bcrypt_not_passlib.md`). ✅
- **Pydantic strict-in / lenient-out** (`feedback_pydantic_strict_input_lenient_output.md`). ✅
- **Manager-fora-da-carteira → 404, não 403** (consistente em todos os endpoints). ✅
- **Decimal sempre** em moeda. ✅
- **Matching guloso** (não ótimo global) — auditabilidade > otimalidade. ✅
- **`/parse` stateless** — sessão só nasce em `POST /reconciliations`. ✅
- **Anomalias estruturais** criadas pelo worker com `detected_by='ai'`. ✅
- **`anomaly_count` = total** (resolvidas + pendentes) — front filtra. ✅
- **Filtro `search` via blind index HMAC** com chave separada. ✅
- **Cache L1 (in-memory 2h, TTLCache cap 10k) + L2 (Redis SETEX 7200)**. ✅
- **`MockOmieClient` via prefixo `FAKE_DEMO_OMIE_`**. ✅
- **`MOCK_PARSE` flag para demos** (com payload Padaria Pão Quente). ✅
- **`processing-screen.tsx` steps por tempo decorrido** (migrar quando S17 chegar). ✅
- **`description_search_hmac=NULL` em sessões pré-S16, sem backfill** (`project_s16_search_blind_index.md`). ✅
- **`.env` editável em dev** (CLAUDE.md §3.13 flexibilizada por dev, retorna em staging/prod — `feedback_env_access_dev_phase.md`). ✅

---

## Apêndice — Como esta auditoria foi conduzida

- **Tempo investido:** ~3.5 horas (intervalo de reboot incluído).
- **Sub-agentes paralelos:** 3 (frontend, reconciliations module, models+migrations+integrations). Outputs validados manualmente em pontos sensíveis antes de transcrever.
- **Ferramentas locais executadas:** `ruff check`, `mypy --strict`, `pytest -q`, grep por padrões inseguros (`eval/exec/shell=True/pickle/yaml.load/text(`).
- **Ferramentas NÃO executadas (limitações):** `pip-audit` (travou sistema — bloqueado em settings.deny pós-incidente), `bandit`/`radon`/`pytest-cov` (falha TLS no `uv pip install`), `pnpm audit`.
- **Leitura primária pelo auditor:** ~30 arquivos do back + ~10 do front + 3 migrations + CLAUDE.md + 1ª metade do PLANO_IMPLEMENTACAO.md.
- **Validações cruzadas com leitura direta:** P0-003 (timing oracle), P0-001 (cookie secure default), P1-001 (paginação Python), P1-002 (anomaly unique), P1-004 (XOR), P1-008 (cache_control), P2-005 (logger redactor nested).
