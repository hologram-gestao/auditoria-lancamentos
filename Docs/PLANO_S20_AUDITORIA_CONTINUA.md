# Plano S20+ — Auditoria Contínua sobre o Omie

> **Status:** 📐 Em planejamento — nenhuma linha escrita. Documento para validação **antes** de codar (CLAUDE.md §6.1).
> **Origem:** transcritos de **08/06/2026** (áudios do **Laio**, citando as provocações do **Galhardo** — gestor de tesouraria/BP financeiro).
> **Relação com o plano principal:** estende [PLANO_IMPLEMENTACAO.md](PLANO_IMPLEMENTACAO.md). As sessões S0–S19 entregam **conciliação file-driven**; este documento abre o eixo **auditoria contínua sobre o Omie** (S20–S27).

---

## 1. O pivot (leia isto primeiro)

O sistema atual nasce de um **arquivo**: upload de extrato/fatura → IA extrai → matching contra Omie → revisão → Excel ([documentation/1](documentation/1.%20Visão%20Geral%20do%20Sistema-20260424133534.md), [documentation/13](documentation/13.%20Processamento%20Atuomático-20260424133737.md)).

Os transcritos descrevem outra coisa — que **engloba** o atual e inverte a prioridade do dia-a-dia. O Laio (citando Galhardo) é explícito:

> _"Por enquanto você não vai ter os extratos bancários para conseguir conciliar... a priori diariamente, por você não ter acesso às contas bancárias, vai precisar ser feita uma curadoria com os lançamentos do próprio sistema [Omie]."_

Ou seja:

- **Hoje (diário/semanal):** auditar a **qualidade dos lançamentos do próprio Omie**, sem extrato. ← **não existe**.
- **Futuro (mensal):** conciliação robusta contra extratos bancários. ← **é o produto atual** (S10–S14).

**Tese de arquitetura:** não tratar isso como ajuste na conciliação, e sim como um motor de **"Audit Run"** que roda checagens sobre o ledger do Omie, com ou sem arquivo. Há muito reuso: o módulo [`qualification`](../apps/api/app/modules/reconciliations/qualification/) (S19) já é ~60% da "análise horizontal" pedida — só está **acoplado a pares conciliados de arquivo** e precisa ser desacoplado para operar direto sobre lançamentos Omie.

---

## 2. Rastreabilidade — pedido do transcrito → sessão

| Pedido (transcrito)                                      | Status hoje                                                                                                                | Sessão               |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | -------------------- |
| Auditar Omie sem extrato (curadoria diária)              | 🔴 falta                                                                                                                   | **S21**              |
| Análise horizontal (fornecedor × categoria entre meses)  | 🟡 acoplada a arquivo ([qualification/historical.py](../apps/api/app/modules/reconciliations/qualification/historical.py)) | **S21** (desacoplar) |
| Duplicadas no mesmo dia                                  | 🟡 catálogo sem detecção                                                                                                   | **S22**              |
| Lançamentos sem departamento classificado                | 🔴 falta (campo nem é lido)                                                                                                | **S22**              |
| Cruzar categoria × natureza / transferência como receita | 🟡 catálogo sem detecção                                                                                                   | **S22**              |
| "Até que dia a conta está conciliada" / tempestividade   | 🔴 falta                                                                                                                   | **S23**              |
| Lembrete de transações recorrentes mensais               | 🔴 falta                                                                                                                   | **S24**              |
| Rotinas diária / semanal / mensal                        | 🔴 falta                                                                                                                   | **S25**              |
| Notificações Slack (interno) + email (cliente)           | 🔴 falta                                                                                                                   | **S26**              |
| Config/taxonomia por cliente ("faz reporte de projeto?") | 🔴 falta                                                                                                                   | **S20**              |
| Contexto/memória por cliente (atas, parâmetros → IA)     | 🔴 falta                                                                                                                   | **S20**              |
| Persona supervisor de BP + monitorar qualidade do time   | 🟡 RBAC só admin/manager                                                                                                   | **S27**              |
| Conciliação mensal com extrato                           | ✅ existe                                                                                                                  | S10–S14              |

---

## 3. Modelo de dados novo (proposto — validar antes de migration)

Segue as regras invioláveis: PK UUID (§3.5), `Decimal(14,2)` em moeda (§3.4), criptografia AES-256-GCM com IV por operação em todo dado identificável do cliente (§4), Omie read-only ([[feedback_omie_read_only]]).

**`client_audit_profiles`** (1:1 com `clients`) — o "mapa de parâmetros" do Galhardo:

- `client_id` (FK único), `require_department` (bool), `does_project_reporting` (bool)
- `staleness_threshold_days` (smallint, default 3), `daily_accounts` (jsonb: `nCodCC[]` que devem ter movimento diário)
- `enabled_checks` (jsonb: lista de `anomaly_types.code` ativos para este cliente)
- `routines` (jsonb: `{daily, weekly, monthly}` on/off + horário)
- `notify_slack_channel` (text), `notify_emails_encrypted`/`_iv` (emails do cliente são identificáveis → cifrar)

**`client_context_notes`** (N por cliente) — atas/taxonomia que alimentam a IA:

- `client_id` (FK), `title` (text), `content_encrypted`/`_iv` (dado do cliente → cifrado)
- `include_in_ai_context` (bool), `created_by`, `created_at`, `updated_at`

**`audit_runs`** — uma execução de auditoria (substitui "session" no eixo sem-arquivo):

- `client_id` (FK), `omie_conta_id` (bigint, nullable = todas as contas), `cadence` (`daily|weekly|monthly|manual`)
- `period_start`/`period_end` (date, em claro — §4.4), `status` (`processing|done|error`), `error_message`
- `trigger_source` (`manual|scheduled`), `triggered_by` (FK users, nullable)
- contadores: `findings_critical|moderate|info`, `started_at`, `finished_at`

**`audit_findings`** — achado de auditoria (espelha `reconciliation_anomalies`, mas amarrado a `audit_runs`; **reusa o catálogo `anomaly_types`**):

- `run_id` (FK), `anomaly_type_id` (FK → catálogo existente), `omie_lancamento_id` (bigint, nullable)
- `omie_conta_id`, `transaction_date` (date claro), `amount` (`Decimal(14,2)` claro)
- `context_encrypted`/`_iv` (motivo/descrição → cifrado), `status` (`open|resolved|ignored`)
- `resolution_note_encrypted`/`_iv`, `resolved_by`, `resolved_at`, `detected_by` (`ai|deterministic`)

**`recurring_transactions`** — recorrências detectadas (S24):

- `client_id`, `omie_conta_id`, `signature_encrypted`/`_iv` (fornecedor+categoria → cifrado)
- `expected_day` (smallint 1–31), `typical_amount` (`Decimal`), `last_seen_month` (date), `confidence` (smallint), `status` (`active|paused`)

**Novos `anomaly_types` no seed** (reusa a tela de gestão S15 — [documentation/16](documentation/16.%20Gestão%20de%20Tipos%20de%20Anomalia-20260424133811.md)):

- `sem_departamento` (moderate), `conta_desatualizada` (moderate), `recorrencia_ausente` (critical)
- Reusa os já existentes: `possible_duplicate`, `inconsistent_category`, `category_mismatch_nature`, `internal_transfer_as_revenue` (hoje sem detector automático).

> ⚠️ **Omie (a confirmar contra response real, nunca contra doc interna — §6.8, [[feedback_omie_validate_response_not_internal_doc]]):** o campo de **departamento/rateio** precisa ser localizado na resposta de `ListarContasPagar/Receber`/`ListarExtrato`. Não existe `listar_departamentos`/`listar_projetos` no cliente atual ([integrations/omie/client.py](../apps/api/app/integrations/omie/client.py) só tem extrato + contas + clientes). Pode ser necessário (a) ler array `distribuicao`/`departamentos` do título e/ou (b) adicionar `ListarDepartamentos` para resolver nomes. Validar com credencial real **antes** da S22.

---

## 4. Sessões

### S20 — Perfil de Auditoria + Memória por Cliente

**Origem:** transcrito 2 ("mapa de parâmetros de lançamentos, especificidades, taxonomia... atas de reuniões dentro do projeto... contexto pra memória do projeto").
**Objetivo:** dar a cada cliente um perfil estruturado de auditoria (quais checks, departamento obrigatório, contas de movimento diário, recorrências, canais de notificação) **e** um repositório de contexto livre (atas/parâmetros) que alimenta a IA nas análises.
**Pré-requisitos:** S6/S7 (cliente já existe). É a fundação dos demais — **fazer primeiro**.
**Duração estimada:** ~1 sessão (6–8 h) — _estimativa por analogia ao S6 (CRUD cliente), back + front simples (§6.15)_.

**Entregáveis:**

1. Migrations: `client_audit_profiles` + `client_context_notes`.
2. Módulo `apps/api/app/modules/audit_profiles/` (`routes/service/repository/schemas`).
3. Endpoints CRUD: `GET/PUT /api/v1/clients/{id}/audit-profile`, `GET/POST/DELETE /api/v1/clients/{id}/context-notes`.
4. Frontend: aba "Auditoria" no detalhe do cliente — form (react-hook-form + zod) do perfil + lista de notas de contexto.
5. RBAC: manager só edita perfil dos clientes da carteira (reusa `require_client_access`).

**DoD:** crypto das notas + emails validada em teste; RBAC negativo (manager fora da carteira → 404); CI verde.
**Fora de escopo:** versionamento de atas; upload de PDF de ata (texto colado por enquanto).

---

### S21 — Motor de Audit Run sobre o Omie (sem arquivo) ⭐ núcleo

**Origem:** transcrito 1 ("curadoria com os lançamentos do próprio sistema"; "análise horizontal mensal"; "garante que o dashboard reflita a realidade diariamente").
**Objetivo:** criar o pipeline que puxa o ledger do Omie de um cliente (conta + período) **sem precisar de extrato** e roda checagens, gerando `audit_findings`. **Desacoplar** o `qualification` para operar sobre lançamentos Omie diretos (não sobre `match_pairs` de arquivo).
**Pré-requisitos:** S20; integração Omie (S5) — reusa `listar_extrato` + `listar_contas_pagar/receber` + cache L1/L2.
**Duração estimada:** ~2 sessões (12–16 h) — _é o maior; mesma ordem do S10/S19 (§6.15)_.

**Entregáveis:**

1. Migrations: `audit_runs` + `audit_findings`.
2. Módulo `apps/api/app/modules/audit/` com `runner.py` (orquestra) + job ARQ (reusa infra de [workers](../apps/api/app/workers/)).
3. `POST /api/v1/clients/{id}/audit-runs` (manual) → dispara job → `GET .../audit-runs/{run_id}` (status + findings).
4. **Refactor do `qualification`:** extrair a lógica histórica/outlier/semântica para aceitar `list[OmieLancamento]` direto, não só `match_pairs`. A camada histórica passa a olhar o ledger Omie de meses anteriores (hoje só olha sessões de conciliação anteriores — [historical.py](../apps/api/app/modules/reconciliations/qualification/historical.py)).
5. Reuso do cache de lançamentos L2 ([lancamento_cache.py](../apps/api/app/integrations/omie/lancamento_cache.py)).

**DoD:** audit run manual sobre cliente real (quando houver credencial — §13 do PLANO) gera findings; teste de integração com Omie mockado (`respx`); falha de uma camada não derruba a run (try/except por camada, padrão do [qualification/service.py](../apps/api/app/modules/reconciliations/qualification/service.py)); CI verde.
**Fora de escopo:** agendamento (S25), notificação (S26), frontend rico (S27 — aqui basta JSON/endpoint).

---

### S22 — Checks determinísticos de qualidade (quick wins)

**Origem:** transcrito 1 ("transações duplicadas no mesmo dia") + transcrito 2 ("lançamentos sem departamento classificado").
**Objetivo:** implementar os checks baratos e de alto valor sobre o motor S21. **Caminho mais curto para entregar valor** — pode ser priorizado logo após o mínimo do S21.
**Pré-requisitos:** S21 (motor). Confirmar campo de departamento no Omie (ver ⚠️ §3).
**Duração estimada:** ~1 sessão (6–8 h) — _checks são funções puras testáveis sem IA (§6.15)_.

**Entregáveis (cada um = uma função pura + teste):**

1. `sem_departamento` — lançamento sem departamento/rateio classificado (respeita `require_department` do perfil; lembrar que rateio = 1 lançamento → N departamentos).
2. `possible_duplicate` (mesmo dia) — mesmo valor + fornecedor + data no mesmo cliente/conta.
3. `category_mismatch_nature` — categoria de despesa em crédito (e vice-versa); reusa normalização `cNatureza` (CLAUDE.md §5.6).
4. `internal_transfer_as_revenue` — transferência entre contas classificada como receita.
5. Registro dos novos tipos no seed + filtro por `enabled_checks` do perfil do cliente.

**DoD:** ≥ 1 teste por check com fixtures determinísticas; respeita `enabled_checks`; CI verde.
**Fora de escopo:** análise horizontal por IA (já no S21 via qualification).

---

### S23 — Tempestividade (freshness de conciliação)

**Origem:** transcrito 1 ("analisar até qual dia aquela conta está conciliada... se normalmente tem lançamento todo dia, por que há 3 dias não tem? → alerta se o time está conciliando ou não").
**Objetivo:** medir, por conta, a data do último lançamento no Omie e alertar quando uma conta de movimento diário fica parada além do limite do perfil.
**Pré-requisitos:** S20 (`daily_accounts`, `staleness_threshold_days`) + S21 (motor).
**Duração estimada:** ~0,5–1 sessão (4–6 h) — _estimativa; check + agregação por conta (§6.15)_.

**Entregáveis:**

1. Check `conta_desatualizada` — gap de N dias úteis sem lançamento numa conta marcada como diária.
2. Indicador "última conciliação/lançamento por conta" no payload da run.

**DoD:** teste com conta parada > limite gera finding, conta ativa não; CI verde.
**Fora de escopo:** distinção feriado/fim de semana sofisticada (usar dias corridos no MVP, marcar como simplificação consciente — §6.17).

---

### S24 — Recorrências e lembretes

**Origem:** transcrito 1 ("o time seja lembrado de transações que acontecem todo mês naquelas datas, pra não esquecer nenhum pagamento").
**Objetivo:** detectar lançamentos recorrentes (mesmo fornecedor/valor/dia do mês) e (a) lembrar antes do vencimento, (b) flagar recorrência esperada que **não** apareceu.
**Pré-requisitos:** S21 (histórico Omie) + S26 (notificação) para o lembrete proativo.
**Duração estimada:** ~1 sessão (6–8 h) — _estimativa; detecção estatística simples (§6.15)_.

**Entregáveis:**

1. Migration `recurring_transactions` + detector (≥ 3 meses, mesmo dia ±janela, valor estável).
2. Check `recorrencia_ausente` na run mensal.
3. (com S26) lembrete N dias antes do `expected_day`.

**DoD:** detector reconhece recorrência sintética em 3 meses; ausência gera finding; CI verde.
**Fora de escopo:** recorrências quinzenais/semanais (só mensais no MVP).

---

### S25 — Rotinas agendadas (diária / semanal / mensal)

**Origem:** transcrito 1 ("rotinas diferentes: diária pra verificação dos lançamentos; semanal pro comportamento da semana / previsto × realizado; mensal com análise horizontal").
**Objetivo:** disparar `audit_runs` por cliente conforme a cadência do perfil, com escopo de checks distinto por cadência.
**Pré-requisitos:** S21–S24. Reusa ARQ + Cloud Scheduler (padrão já dominado — [[project_retomada_2026_06_07]], [[feedback_cron_*]]).
**Duração estimada:** ~1 sessão (6–8 h) — _estimativa por analogia ao cleanup Job já existente (§6.15)_.

**Entregáveis:**

1. Scheduler que itera clientes com rotina ligada e enfileira runs (diária = freshness + duplicadas; semanal = previsto×realizado + esquecimentos; mensal = horizontal + qualificação completa).
2. Cloud Scheduler → endpoint/Job interno (mesmo padrão do `auditoria-cleanup-stuck`).
3. Guard de idempotência (não duplicar run da mesma cadência/dia).

**DoD:** dry-run gera as runs esperadas por cadência; idempotência testada; CI verde.
**Fora de escopo:** UI de configuração fina de horário (usa defaults no MVP).

---

### S26 — Notificações (Slack interno + email cliente)

**Origem:** transcrito 1 ("Slack pra interno — todos têm acesso, integração com o cloud; email pro cliente quando for mandar relatório; WhatsApp opcional").
**Objetivo:** entregar resultados/alertas de auditoria nos canais certos. **Slack primeiro (interno).**
**Pré-requisitos:** S25 (algo que dispare a notificação). Decisão pendente: app Slack vs webhook; provedor de email (ver §5).
**Duração estimada:** ~1 sessão (6–8 h) — _estimativa; depende da decisão de provedor (§6.15)_.

**Entregáveis:**

1. Abstração `Notifier` com canais plugáveis (`SlackChannel`, `EmailChannel`) — **não amarrar no Slack**.
2. Slack: resumo da run com findings críticos no canal do perfil.
3. Email (cliente): envio de relatório — **respeitar §3 (sem vazar credencial), confirmar com humano antes de enviar ao cliente** (ação outward-facing).
4. `notifications` log (status/erro, payload redigido — §3.3).

**DoD:** Slack de teste recebe mensagem; falha de canal não derruba a run; segredos do Slack/email só em env (§3.1); CI verde.
**Fora de escopo:** WhatsApp (registrar como follow-up).

---

### S27 — Frontend de Auditoria + persona supervisor

**Origem:** transcrito 1 ("Galhardo hoje, em breve uma pessoa na supervisão do BP... precisa das ferramentas pra auditar e garantir que a auditoria aconteça junto ao time").
**Objetivo:** telas para o supervisor revisar findings, ver tempestividade por cliente/conta e acompanhar a qualidade do time.
**Pré-requisitos:** S21–S25.
**Duração estimada:** ~2 sessões (12–16 h) — _estimativa por analogia à tela de revisão S12/S13 (§6.15)_.

**Entregáveis:**

1. Dashboard de auditoria (findings abertos por cliente/severidade/cadência), reusa TanStack Query + Table + virtualização (>100 linhas — CLAUDE.md §7 front).
2. Tela de uma `audit_run` (findings, resolver/ignorar com nota cifrada — espelha [documentation/14](documentation/14.%20Tela%20de%20Revisão-20260424133749.md)).
3. Visão de tempestividade por conta.
4. **Decisão de RBAC:** avaliar role `supervisor` vs. reusar `admin`/`manager` com escopo (ver §5).

**DoD:** golden path (login supervisor → dashboard → run → resolver finding) testado; CI verde.
**Fora de escopo:** métricas de produtividade individual por analista (Fase 2 — sensível, alinhar com Laio antes).

---

## 5. Decisões em aberto (não decidir sozinho — §6.2)

- [ ] **Departamento no Omie:** onde o campo vive na response real? Precisa de `ListarDepartamentos`? _(bloqueia S22)_
- [ ] **Persona supervisor:** role nova `supervisor` ou reuso de `admin`/`manager`? _(S27)_
- [ ] **Slack:** Slack App (Bot token + chat.postMessage) ou Incoming Webhook por canal? _(S26)_
- [ ] **Email:** qual provedor (SES, SendGrid, SMTP do Google Workspace)? Envio ao cliente exige aprovação humana por run ou é automático? _(S26)_
- [ ] **Audit Run × conta:** uma run por conta ou uma run cobrindo todas as contas do cliente? _(S21)_
- [ ] **Credencial Omie real** para validar campos (departamento, paginação `ListarExtrato`) — já listado no PLANO §13. _(S21/S22)_
- [ ] **"Previsto × realizado" semanal:** definição exata do que conta como "esquecido" (depende da semântica de status Omie — CLAUDE.md §5.7). _(S25)_

---

## 6. Ordem recomendada

```
S20 (perfil/contexto)  →  S21 (motor)  →  S22 (checks quick-win)  ──┐
                                       →  S23 (tempestividade)       │→ valor já visível
                                       →  S24 (recorrências)         │
                              S25 (rotinas) → S26 (notificações) ────┘
                                       →  S27 (frontend supervisor)
```

**Quick-win mais curto até valor demonstrável:** mínimo do S21 + S22 (checks `sem_departamento` + `possible_duplicate` rodando sobre o Omie via run manual). O resto operacionaliza (agenda, notifica, visualiza).

---

_Documento vivo — atualizar ao validar o recorte com Laio/Galhardo e ao fechar cada sessão._
