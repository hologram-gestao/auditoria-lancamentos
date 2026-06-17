# Plano dos Próximos Passos — FASE 0 a 5 (ADL Hologram)

> **Versão 1.0 — 15/06/2026.** Plano de implementação derivado do PRD
> [Docs/NextSteps/PRD - Próximos Passos-20260615173056.md](NextSteps/PRD%20-%20Pr%C3%B3ximos%20Passos-20260615173056.md)
> (PM Lucas Landim; stakeholders Laio Brito, Galhardo, Pedro Silva).
>
> **O que este documento é:** a quebra do PRD em **sessões de implementação** numeradas
> continuando a sequência do projeto (S20 em diante), no mesmo formato das sessões S0–S19
> do [PLANO_IMPLEMENTACAO.md](PLANO_IMPLEMENTACAO.md). O PRD é o **quê/porquê**; este plano é o **como/ordem**.
>
> **Relação com os planos anteriores:**
>
> - [PLANO_IMPLEMENTACAO.md](PLANO_IMPLEMENTACAO.md) — entregou **S0–S19** (conciliação file-driven, em dev). Continua válido como histórico do que está construído.
> - [PLANO_S20_AUDITORIA_CONTINUA.md](PLANO_S20_AUDITORIA_CONTINUA.md) — **SUPERSEDED por este documento.** O eixo de auditoria contínua que ele propunha (antigo S20–S27) foi **absorvido na FASE 5** abaixo, com duas mudanças: (a) **sem Redis/ARQ** — agendamento via **Cloud Scheduler → Cloud Run Job** (padrão já em uso); (b) **reposicionado para médio/longo prazo**, depois de cartão e Pluggy. O mapeamento antigo→novo está em [§ Anexo A](#anexo-a--mapa-do-antigo-s20s27--fase-5).
>
> **Disciplina de fonte da verdade (CLAUDE.md §6.19):** este plano descreve **intenção**. O código atual é a fonte da verdade. Onde um passo depende de comportamento externo não verificado (API Omie de escrita, campos do Pluggy, campo de departamento no Omie), está marcado **(a confirmar)** — validar contra response real **antes** de implementar, nunca contra doc interna (CLAUDE.md §6.7/§6.8).

---

## Sumário

- [Roadmap e prioridades](#roadmap-e-prioridades)
- [Estado dos bugs da FASE 0 (já resolvidos)](#estado-dos-bugs-da-fase-0)
- [FASE 0 — Estabilização (S20)](#fase-0--estabilização)
- [FASE 1 — Conciliação de faturas de cartão (S21–S23)](#fase-1--conciliação-de-faturas-de-cartão)
- [FASE 2 — Lançamento automático no Omie (S24–S25)](#fase-2--lançamento-automático-no-omie)
- [FASE 3 — Glossário e classificação por cliente (S26–S27)](#fase-3--glossário-e-classificação-por-cliente)
- [FASE 4 — Open Finance via Pluggy (S28–S31)](#fase-4--open-finance-via-pluggy)
- [FASE 5 — Rotinas automáticas de auditoria (S32–S38)](#fase-5--rotinas-automáticas-de-auditoria)
- [Decisões em aberto (não decidir sozinho)](#decisões-em-aberto)
- [Anexo A — mapa do antigo S20–S27 → FASE 5](#anexo-a--mapa-do-antigo-s20s27--fase-5)

---

## Roadmap e prioridades

Ordem de prioridade conforme o PRD (`## ROADMAP`):

```
FASE 0  Estabilização ............ URGENTE — pré-requisito de tudo (só falta remover Redis)
FASE 1  Conciliação de cartão .... ALTA — maior dor do BPO hoje (Galhardo)
FASE 2  Lançamento auto no Omie ... ALTA — fecha o ciclo do cartão (depende de FASE 1 estável em prod)
FASE 3  Glossário por cliente ..... MÉDIA — contexto p/ classificação assertiva
FASE 4  Open Finance (Pluggy) ..... MÉDIO PRAZO — depende de decisão externa (Cubos, 16/06)
FASE 5  Rotinas automáticas ....... LONGO PRAZO — absorve o antigo S20–S27, sem Redis
```

**Dependências entre fases:**

```
FASE 0 ──► FASE 1 ──► FASE 2
                       └─ sugestão de categoria usa FASE 3 (glossário) — dependência fraca
           FASE 3 ──────────────► FASE 5 (análise horizontal precisa de contexto/cliente)
           FASE 4 ──────────────► FASE 5 (rotinas diárias muito mais fortes com dados bancários automáticos)
```

**Caminho mais curto até valor demonstrável:** FASE 0 (destravar) → FASE 1 (conciliar cartão ponta a ponta). É o que o Galhardo chama de _"maior gargalo"_.

---

## Estado dos bugs da FASE 0

O PRD lista dois bugs na FASE 0, mas **ambos já foram corrigidos e testados** antes desta data — verificado contra código + git. Tratá-los como **feitos**, não re-fazer:

| Bug do PRD                                           | Status       | Evidência                                                                                                                                                                                                                                                                      |
| ---------------------------------------------------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Auth JWT não persiste após login (logout ao navegar) | ✅ Resolvido | PR **#19** / `cf44cea` (13/06): cookie vive a sessão (7d em vez de 60min), refresh resiliente a 5xx/rede, bootstrap com retry. Testes: [test_auth_cookies.py](../apps/api/tests/unit/test_auth_cookies.py), [client.test.ts](../apps/web/src/lib/api/__tests__/client.test.ts) |
| Timeout da Claude API (limite "2 min")               | ✅ Resolvido | PR **#16** / `8bd1e6a` (09/06): backend 60s→150s ([config.py:112-119](../apps/api/app/core/config.py#L112-L119)), proxy Next 30s→160s ([next.config.mjs:97-104](../apps/web/next.config.mjs#L97-L104))                                                                         |

> O PRD foi escrito em 15/06 provavelmente sem ciência dos fixes de 09/06 e 13/06. **A única coisa que falta na FASE 0 é a remoção do Redis** (S20). Se na prática o logout/timeout ainda aparecer, reabrir como investigação separada (possível deploy não propagado ou caso não coberto).

---

## FASE 0 — Estabilização

### S20 — Remoção do Redis/ARQ → BackgroundTasks nativo ✅ CONCLUÍDA (16/06/2026, [BACK 0.1])

> **Status:** implementado no código. Falta apenas o passo de **ops/deploy** (decomissionar o service `auditoria-worker-dev` + Upstash, e setar `--no-cpu-throttling` + `min-instances ≥ 1` no `auditoria-api-dev`) e a validação ponta a ponta com dados reais (extrato da Austral). Gate local verde: ruff + ruff format + mypy (95 arquivos) + **507 pytest passed**.

**Origem:** PRD FASE 0 — _"o Redis foi identificado como overengineering para o volume atual. O Upstash atingiu o limite e travou o sistema."_

**Objetivo:** eliminar Redis e ARQ. O processamento assíncrono da conciliação (busca Omie + matching + anomalias + qualificação) passa a rodar em **`BackgroundTasks` nativo do FastAPI**, no mesmo processo da API. Sem broker, sem worker dedicado, sem Upstash.

**Pré-requisitos:** nenhum (é o destravamento). **Duração estimada:** ~1 sessão (5–7 h) — _o mapa de dependências é pequeno: 1 job só, polling já lê do DB._

**Contexto de arquitetura (reversão consciente):** o [PLANO_IMPLEMENTACAO.md §6.3/§6.4](PLANO_IMPLEMENTACAO.md) recomendava explicitamente _"jobs via broker desde o MVP (não FastAPI.BackgroundTasks, que morrem com o processo)"_. O PRD reverte isso para o volume atual (sessões manuais, uma por vez). A rede de segurança contra "task morre com o processo" **já existe**: o cron de cleanup marca sessões `processing` há > 25 min como `error` ([mark_stuck_sessions_as_error.py](../apps/api/scripts/mark_stuck_sessions_as_error.py)), e ele roda por **Cloud Scheduler → Cloud Run Job** (não por ARQ), então **sobrevive** à remoção do Redis.

**Entregáveis:**

1. **Mover o job para BackgroundTasks:**
   - [processing/job.py](../apps/api/app/modules/reconciliations/processing/job.py) — `run_reconciliation_processing(ctx, session_id)` deixa de ser task ARQ (com `ctx`) e vira um `async def run_reconciliation(session_id)` chamável direto.
   - [processing/dispatcher.py](../apps/api/app/modules/reconciliations/processing/dispatcher.py) — `enqueue_processing()` (que cria pool ARQ e `enqueue_job`) é removido/substituído por agendamento via `BackgroundTasks.add_task`.
   - [reconciliations/routes.py](../apps/api/app/modules/reconciliations/routes.py) — `create_reconciliation` e `reprocess_reconciliation` injetam `BackgroundTasks` e fazem `background_tasks.add_task(run_reconciliation, session_id)` em vez de `_enqueue_reconciliation_job`.
2. **Remover infra de fila:**
   - Deletar [workers/arq_worker.py](../apps/api/app/workers/arq_worker.py), [workers/entrypoint.py](../apps/api/app/workers/entrypoint.py), [scripts/run_worker.py](../apps/api/scripts/run_worker.py), [core/redis_config.py](../apps/api/app/core/redis_config.py).
   - `pyproject.toml` — remover deps `arq` e `redis`.
3. **Cache de lançamentos Omie → L1-only:**
   - [main.py](../apps/api/app/main.py) (lifespan, ~L61-68) — remover init/close do Redis. `OmieLancamentoCache` já degrada para L1 quando `redis=None` ([lancamento_cache.py](../apps/api/app/integrations/omie/lancamento_cache.py)). Como o matching agora roda no mesmo processo, L1 é suficiente.
   - [core/config.py](../apps/api/app/core/config.py) — remover `REDIS_URL` e `CACHE_BACKEND` (ou deixar `CACHE_BACKEND=MEMORY` como única opção).
4. **Infra / deploy:**
   - [docker/docker-compose.yml](../docker/docker-compose.yml) — remover serviço `redis` e `worker`.
   - [.github/workflows/deploy-dev.yml](../.github/workflows/deploy-dev.yml) — remover o job `deploy-worker` e a env `WORKER_SERVICE`. Manter `CLEANUP_JOB` (continua útil).
   - **Cloud Run:** decomissionar o service `auditoria-worker-dev` e o **Upstash Redis**. Garantir no service `auditoria-api-dev`: `--no-cpu-throttling` **e** `min-instances ≥ 1` (ver gotcha abaixo).
5. **Atualizar docs de stack:** CLAUDE.md §2 e §9 (tirar ARQ/Redis) + PLANO_IMPLEMENTACAO §6.3/§6.4 — **na mesma entrega** (CLAUDE.md §13).

**⚠️ Gotcha de Cloud Run (flag obrigatório):** `BackgroundTasks` roda **depois** da resposta HTTP, no mesmo container. No Cloud Run, fora de uma request ativa a CPU é estrangulada e a instância pode ser reciclada (scale-to-zero) — a task **congela ou morre**. Mitigações na S20: (a) `--no-cpu-throttling` no service da API (hoje só o worker tem); (b) `min-instances ≥ 1` para não reciclar no meio do job; (c) o cron de cleanup (25 min) continua como rede de segurança. Documentar isso no PR e no runbook.

**Fora de escopo:** tornar o `/parse` assíncrono — ele continua síncrono (chamada Claude na request, timeout 150s já alinhado com o proxy). Só o pós-parse (Omie + matching + qualificação) vai para background.

**DoD:**

- [x] `arq`/`redis` removidos do código (deps, workers/, dispatcher, redis_config, run_worker) e do lock.
- [ ] Conciliação ponta a ponta (upload → parse → confirma → processing → reviewing → export) **sem Redis** em dev — _pendente: validar pós-deploy com dados reais (extrato Austral)._
- [x] `docker compose up` sobe sem Redis/worker (compose ajustado).
- [x] Testes do fluxo de processamento passam (ajustados para chamar o job direto, sem `ctx` ARQ) — 47 integração + 273 unit.
- [x] CI local verde; CLAUDE.md (§2/§8/§9/§10) e este plano atualizados.

---

## FASE 1 — Conciliação de faturas de cartão

> **Tese:** reusar o pipeline de conta corrente (upload → IA → cruzamento → revisão) com adaptações para cartão. O sistema **já distingue tipo de conta** no Omie (`CC` corrente, `CR` cartão, `CA` aplicação — [omie_account_cache.py:29-45](../apps/api/app/db/models/omie_account_cache.py#L29-L45), DTO [schemas.py:103-133](../apps/api/app/integrations/omie/schemas.py#L103-L133)), e o label de cartão já aparece no form (`formatAccountLabel` marca `CR` com "(Cartão)"). A base existe — a FASE 1 é adaptação, não greenfield.

**Critério de sucesso (PRD):** operador concilia uma fatura de cartão **real** ponta a ponta, sem subir o extrato da conta corrente, e exporta o relatório em **< 5 min**.

### S21 — Tolerância de data zero + status `conciliado_data_divergente` + anomalia `wrong_date`

**Origem:** PRD FASE 1, _"Regra de data"_ e _"Remoção do campo de tolerância de data"_ (Laio: _"extrato bancário é tolerância zero"_).

**⚠️ Delta importante (CLAUDE.md §6 — registrar contradição):** o PRD afirma _"tolerância zero, igual à conta corrente"_, mas o **código atual de conta corrente usa tolerância parametrizável (default 3 dias)** — [matcher.py:82/126](../apps/api/app/modules/reconciliations/processing/matcher.py#L82), coluna `date_tolerance_days` ([reconciliation_session.py:99](../apps/api/app/db/models/reconciliation_session.py#L99)). Logo, esta sessão **muda o comportamento da conciliação de conta corrente que já roda em prod**, não só cartão. Confirmar com stakeholder antes de implementar (ver [Decisões em aberto](#decisões-em-aberto)). Isto exige atualizar as **Regras Invioláveis de Domínio** (CLAUDE.md §5.2/§5.3) no mesmo PR.

**Nova semântica do matcher (proposta):**

- Match por **valor** (tolerância `≤ 0.01 BRL`, inalterada — CLAUDE.md §5.1).
- Data **igual** → `situation='conciliado'`.
- Valor casa, **data diferente** → `situation='conciliado_data_divergente'` + anomalia `wrong_date` (não deixa de conciliar silenciosamente; o operador decide).
- Sem casamento de valor → `sem_omie` (inalterado).

**Entregáveis:**

1. **Matcher** ([matcher.py](../apps/api/app/modules/reconciliations/processing/matcher.py)) — remover `tolerance_days`; implementar a semântica acima (período Omie buscado sem expansão por tolerância, ou expansão fixa pequena só para capturar bordas — definir na sessão).
2. **DB** — migration removendo `date_tolerance_days` de `reconciliation_sessions`; novo valor de enum `CONCILIADO_DATA_DIVERGENTE` em `FileEntrySituation` ([reconciliation_file_entry.py:39-44](../apps/api/app/db/models/reconciliation_file_entry.py#L39-L44)).
3. **Catálogo de anomalias** — novo tipo `wrong_date` (severity `moderate`, `detected_by='ai'`/`deterministic`) no seed ([seed_dev.py:52+](../apps/api/scripts/seed_dev.py#L52)); geração automática em [anomalies.py](../apps/api/app/modules/reconciliations/processing/anomalies.py).
4. **Backend schema** — remover `date_tolerance_days` de [schemas.py:103](../apps/api/app/modules/reconciliations/schemas.py#L103).
5. **Frontend form** — remover o campo de tolerância ([new-reconciliation-form.tsx:433-480](../apps/web/src/components/features/reconciliations/new-reconciliation-form.tsx#L433-L480)) e o zod ([reconciliations.ts:17-19](../apps/web/src/lib/validation/reconciliations.ts#L17-L19)).
6. **Tela de revisão** — exibir o novo status e a anomalia `wrong_date`.

**DoD:** teste de regressão da conta corrente (datas iguais ainda conciliam; data diferente vira `conciliado_data_divergente` + `wrong_date`); migration reversível; CLAUDE.md §5 atualizado; CI verde.

### S22 — Parsing IA com particularidades de cartão

**Origem:** PRD FASE 1, _"Particularidades da fatura de cartão"_.

**Objetivo:** ensinar a extração da IA a tratar fatura de cartão corretamente. A infra já suporta `account_type` (`checking`/`credit_card`) no tool e no prompt ([tools.py:24-107](../apps/api/app/integrations/anthropic/tools.py#L24-L107), [prompts.py:19-55](../apps/api/app/integrations/anthropic/prompts.py#L19-L55), [schemas.py:48-92](../apps/api/app/integrations/anthropic/schemas.py#L48-L92)). O trabalho é refinar o guia de extração.

**Particularidades a codificar no prompt/schema:**

- Transações = débitos (saldo negativo); **estornos = créditos** (positivos).
- **Parcelas individualizadas** por data real de cada parcela — não agrupar.
- **Encargos** (juros, IOF, multa) = transações separadas com descrição específica.
- O **pagamento da fatura não é transação da fatura** (aparece no extrato da conta corrente como `DEB.CTA.FATURA`) — não deve ser extraído como linha da fatura.
- Fatura de cartão normalmente não traz `balance` por linha (já é `optional` no schema).

**Entregáveis:** ajuste do system/user prompt e, se necessário, do tool schema para o caso `credit_card`; testes de extração com fatura real (respeitando particularidades).

**DoD:** parse de fatura de cartão real extrai parcelas separadas, estornos como crédito, encargos como linhas próprias, e não inclui o pagamento da fatura; teste cobrindo ao menos parcela + estorno.

### S23 — UX de revisão adaptada ao cartão

**Origem:** PRD FASE 1, _"Experiência do usuário"_.

**Objetivo:** do ponto de vista do operador o fluxo é idêntico ao da conta corrente; as diferenças visíveis são pontuais.

**Entregáveis:**

1. **Form** — conta de cartão (`CR`) selecionável e rotulada `Cartão` (já existe o label; garantir o fluxo).
2. **Header da revisão** — exibir o tipo de conta (Conta corrente / Cartão).
3. **Filtros da aba de movimentações** — quando `account_type='credit_card'`, trocar `Créditos`/`Débitos` por `Compras`/`Estornos` ([movements-tab.tsx:215-229](../apps/web/src/components/features/reconciliations/review/movements-tab.tsx#L215-L229)). Labels condicionais ao tipo de conta; a semântica do filtro (sinal do valor) é a mesma.

**DoD:** sessão de cartão mostra `Compras`/`Estornos` e tipo no header; sessão de conta corrente continua `Créditos`/`Débitos`; CI verde.

---

## FASE 2 — Lançamento automático no Omie

> **Dependência (PRD):** requer FASE 1 **estável e validada em produção com dados reais** antes de iniciar. A sugestão de categoria (S25) usa o glossário da **FASE 3** + histórico — dependência fraca; sem FASE 3, sugerir só por histórico.

**Critério de sucesso (PRD):** operador lança e concilia uma fatura de cartão completa no Omie **através do ADL, sem acesso direto ao Omie**.

### S24 — Camada de escrita no Omie (`IncluirContaPagar`)

**⚠️ Quebra de invariante (flag obrigatório):** hoje o cliente Omie é **100% read-only** (só `Listar*` — [client.py:506-707](../apps/api/app/integrations/omie/client.py#L506-L707)) e a memória do projeto registra _"Omie read-only"_ como princípio ([[feedback_omie_read_only]]). A FASE 2 introduz **escrita** no Omie. É uma decisão deliberada do PRD, mas deve ser tratada com cuidado: confirmação explícita do operador antes de cada criação, idempotência para não duplicar em retry, e atualização da memória/CLAUDE.md registrando que a escrita passa a ser permitida **apenas neste fluxo**.

**Entregáveis:**

1. Método de escrita no `OmieClient` reusando o envelope genérico `call()` ([client.py:206-212](../apps/api/app/integrations/omie/client.py#L206-L212)): `incluir_conta_pagar(...)` **(a confirmar contra Omie real — §6.8)** o endpoint (`financas/contapagar/IncluirContaPagar`), campos obrigatórios (fornecedor, valor, vencimento, categoria, `nCodCC`), e o campo de idempotência (`codigo_lancamento_integracao`) para retry seguro.
2. DTOs Pydantic de request/response para a inclusão ([integrations/omie/schemas.py](../apps/api/app/integrations/omie/schemas.py) só tem DTOs de leitura hoje).
3. Tratamento de erro Omie na escrita (faultstring) com mensagem PT ao operador.

**DoD:** criação de 1 conta a pagar de teste em conta Omie real (quando houver credencial — PLANO §13); idempotência testada (retry não duplica); credenciais nunca logadas; CI verde.

### S25 — Fluxo integrado lançar → conciliar (com confirmação do operador)

**Origem:** PRD FASE 2, _"Fluxo"_.

**Objetivo:** após o parse da fatura e a confirmação da prévia, o ADL oferece **lançar as transações no Omie** antes de conciliar; depois concilia automaticamente com os lançamentos recém-criados.

**Fluxo (PRD):**

1. Upload da fatura → confirma prévia do parsing.
2. ADL pergunta: _"Deseja lançar as transações no Omie antes de conciliar?"_ (operador pode escolher só conciliar, se já lançou manualmente).
3. Se sim: ADL propõe categoria por transação (glossário FASE 3 + histórico); operador revisa/ajusta/confirma.
4. ADL cria os lançamentos no Omie (S24).
5. ADL concilia automaticamente.
6. Operador cai direto na tela de revisão.

**Particularidades:** transação sem fornecedor identificado → pendente de preenchimento manual antes de confirmar; estorno = crédito (operador decide estornar lançamento existente ou criar novo); **o ADL nunca cria lançamento sem confirmação explícita**.

**DoD:** fluxo lançar→conciliar completo em conta de teste; sugestão de categoria editável; nada é criado no Omie sem confirmação; CI verde.

---

## FASE 3 — Glossário e classificação por cliente

> **Sobreposição planejada:** o antigo S20 (PLANO_S20) propunha `client_audit_profiles` + `client_context_notes`. A FASE 3 foca o **glossário/plano de contas + regras por cliente**; a parte de **perfil de rotinas** (contas diárias, cadências) vai para a FASE 5 (S32). Reusar o desenho de criptografia de notas do antigo S20.

**Critério de sucesso (PRD):** operador cadastra categorias com descrição para um cliente e, ao revisar, vê o glossário como referência ao analisar anomalias de classificação.

### S26 — Plano de contas por cliente (model + CRUD + aba no detalhe)

**Origem:** PRD FASE 3 (Galhardo: _"um glossário dizendo qual é a função de cada uma das categorias"_).

**Entregáveis:**

1. **Model** novo (padrão de [omie_account_cache.py:47-71](../apps/api/app/db/models/omie_account_cache.py#L47-L71): UUID PK, FK `client_id` com `ondelete=CASCADE`, `TimestampMixin`): categoria, descrição de uso, fornecedores típicos, restrições. **Campos identificáveis do cliente → criptografados** (AES-256-GCM, IV por operação — CLAUDE.md §4).
2. **Módulo** `apps/api/app/modules/client_glossary/` (`routes/service/repository/schemas`), RBAC reusando `require_client_access`.
3. **Frontend** — o detalhe do cliente é **linear hoje** (sem abas — [client-detail-client.tsx](../apps/web/src/components/features/clients/client-detail-client.tsx)). Introduzir layout de abas (Contas / Glossário / Histórico) e a aba de glossário com form `react-hook-form + zod`.

**DoD:** CRUD do glossário com crypto validada em teste; RBAC negativo (manager fora da carteira → 404); aba renderiza no detalhe; CI verde.

### S27 — Regras de auditoria por cliente + injeção no prompt da qualificação

**Origem:** PRD FASE 3, _"Regras de auditoria por cliente"_ (ex.: _"IOF nunca classificado como juros"_).

**Entregáveis:**

1. Model de regras por cliente (cadastro estruturado).
2. **Injeção do glossário + regras no contexto da IA** na qualificação semântica — hoje o contexto vai como _tool input_ por par ([qualification/semantic.py:54-99](../apps/api/app/modules/reconciliations/qualification/semantic.py#L54-L99)). Estender para incluir glossário/regras do cliente.
3. Nesta fase, as regras são **exibidas como referência** na revisão (aplicação automática pela IA fica para fase futura — PRD).

**DoD:** regra cadastrada aparece como referência na revisão; qualificação recebe o glossário no contexto; CI verde.

---

## FASE 4 — Open Finance via Pluggy

> **Nada existe hoje** (grep por `pluggy`/`webhook`/`open finance` = zero). Todos os endpoints exigem auth ([dependencies.py:58-96](../apps/api/app/core/dependencies.py#L58-L96)); só `/health` é público. A FASE 4 introduz o **primeiro endpoint público (webhook)** — nova superfície de segurança.

**Critério de sucesso (PRD):** Hologram conecta ≥ 3 bancos de clientes reais via Pluggy e processa a conciliação mensal de um cliente completo (todas as contas) **sem nenhum upload manual**.

### S28 — Decisão (interna vs Cubos) + fundação de dados

**Origem:** PRD FASE 4, _"Dependências e riscos"_.

**Decisões/riscos a resolver antes de codar:**

- **Integração interna vs parceria Arthur Souza (Cubos)** — proposta esperada **16/06/2026**. Se inviável, fazer interno (Pluggy tem SDK Python).
- **Cobertura de bancos** — confirmar Sicredi, BNB e Cora nos conectores Pluggy; bancos sem conector mantêm upload manual como fallback permanente.

**Entregáveis:** tabela `client_pluggy_connections` (schema no PRD — `client_id`, `omie_conta_id`, `pluggy_item_id`, `pluggy_account_id`, `bank_name`, `account_type` ∈ {`checking`,`credit_card`}, `consent_expires_at`, `last_sync_at`, `status` ∈ {`active`,`expired`,`error`}), seguindo o padrão de model do projeto. Credenciais/tokens Pluggy só em env (CLAUDE.md §3).

### S29 — Conexão: connect token + Pluggy Connect widget

**Origem:** PRD FASE 4, fluxo passo 1.

**Entregáveis:** endpoint backend que gera `connect_token` server-side (API keys Hologram, nunca no browser); embed do **Pluggy Connect** no frontend; persistência do `itemId` vinculado a `client_id` + `omie_conta_id`. O ADL **nunca** toca nas credenciais bancárias (o widget cuida de MFA/erros por instituição).

### S30 — Webhook `item/updated` + ingestão → pipeline de conciliação

**Origem:** PRD FASE 4, fluxo passo 2; casos de uso A (conta corrente: `Account`+`Transaction`) e B (cartão: `Credit Card Bills`+`Credit Card Installments`).

**Entregáveis:** endpoint **público** de webhook (verificação de assinatura/segredo Pluggy — primeiro endpoint sem auth de usuário; revisar §5 de segurança do PLANO); ao receber `item/updated`, buscar transações do período via API Pluggy e disparar conciliação (Caso A) ou propor lançamento+conciliação (Caso B, reusa FASE 1/2). Reage a evento, sem polling.

**DoD:** webhook assinado validado; payload redigido em log; ingestão de teste dispara conciliação; CI verde.

### S31 — Gestão de consent + notificação de expiração

**Origem:** PRD FASE 4, fluxo passo 3 (_"Consent... data de expiração regulada pelo Banco Central"_).

**Entregáveis:** monitorar `consent_expires_at` por conexão; N dias antes da expiração, notificar o operador (via Slack — usa o `Notifier` da FASE 5); status da conexão (ativa/expirada/erro) exibido no detalhe do cliente ao lado do histórico. Fallback de upload manual mantido.

---

## FASE 5 — Rotinas automáticas de auditoria

> **Esta fase absorve o antigo [PLANO_S20_AUDITORIA_CONTINUA.md](PLANO_S20_AUDITORIA_CONTINUA.md) (S20–S27 antigos)**, com duas mudanças: **sem Redis/ARQ** (agendamento via **Cloud Scheduler → Cloud Run Job**, o mesmo padrão do cleanup [mark_stuck_sessions_as_error.py](../apps/api/scripts/mark_stuck_sessions_as_error.py)) e **reposicionada para depois de cartão e Pluggy**. O mapeamento sessão-a-sessão está no [Anexo A](#anexo-a--mapa-do-antigo-s20s27--fase-5). O conteúdo de rastreabilidade dos transcritos (Galhardo/Laio) permanece válido no doc antigo como material de origem.

**Critério de sucesso (PRD):** o time BPO recebe alertas automáticos no Slack antes de checar manualmente; a rotina mensal identifica ≥ 1 inconsistência de classificação horizontal por cliente por ciclo.

**Dependências:** FASE 3 (glossário — contexto p/ análise horizontal) e idealmente FASE 4 (Pluggy — sem ele, as rotinas diárias só olham o Omie, sem comparar com o banco). _"Cloud Scheduler: disparador das rotinas sem Redis, sem fila"_ (PRD).

### S32 — Perfil de auditoria por cliente (cadências, contas diárias, canais)

`client_audit_profiles` (do antigo S20, menos a parte de glossário que virou FASE 3): `require_department`, `daily_accounts` (contas que devem ter movimento diário), `staleness_threshold_days`, `enabled_checks`, `routines` (daily/weekly/monthly on/off), `notify_slack_channel`, `notify_emails_encrypted`.

### S33 — Motor de Audit Run sobre o Omie (sem arquivo) ⭐ núcleo

`audit_runs` + `audit_findings` (reusa o catálogo `anomaly_types`). **Desacoplar a qualificação** ([qualification/historical.py](../apps/api/app/modules/reconciliations/qualification/historical.py)) para operar sobre lançamentos Omie diretos (`list[OmieLancamento]`), não só sobre `match_pairs` de arquivo. Run manual primeiro; cache L1 (sem Redis).

### S34 — Checks determinísticos (quick wins)

`sem_departamento` **(a confirmar onde vive o campo de departamento/rateio na response Omie — §6.8)**, `possible_duplicate` (mesmo dia), `category_mismatch_nature`, `internal_transfer_as_revenue`. Cada um = função pura testável; filtra por `enabled_checks` do perfil.

### S35 — Tempestividade (freshness por conta)

`conta_desatualizada` — conta de movimento diário parada além do `staleness_threshold_days`. Indicador "última conciliação/lançamento por conta".

### S36 — Recorrências e lembretes

`recurring_transactions` (≥ 3 meses, mesmo dia ±janela, valor estável); check `recorrencia_ausente`; lembrete N dias antes do vencimento (via Notifier S37).

### S37 — Rotinas agendadas + Notificações (Slack interno + email cliente)

Scheduler que itera clientes com rotina ligada e dispara `audit_runs` por cadência (diária = freshness + duplicadas; semanal = previsto×realizado; mensal = horizontal + qualificação). **Cloud Scheduler → Cloud Run Job** (padrão do cleanup), com guard de idempotência (não duplicar run da cadência/dia). Abstração `Notifier` plugável (`SlackChannel`, `EmailChannel`) — **não amarrar no Slack**; Slack interno primeiro; email ao cliente exige aprovação humana por run (ação outward-facing — CLAUDE.md). Hoje **não há** Slack nem email no código.

### S38 — Frontend de auditoria + persona supervisor

Dashboard de findings (por cliente/severidade/cadência, virtualizado), tela de uma `audit_run` (resolver/ignorar com nota cifrada, espelha a tela de revisão), visão de tempestividade. Decisão de RBAC: role `supervisor` nova vs. reuso de `admin`/`manager` com escopo.

---

## Decisões em aberto

> Não decidir sozinho (CLAUDE.md §6.2/§10). Itens novos que o PRD levanta, além dos já listados no PLANO_IMPLEMENTACAO §13:

- [ ] **(FASE 0)** Cloud Run da API com `--no-cpu-throttling` + `min-instances ≥ 1` é aceitável em custo? Sem isso, BackgroundTasks pode congelar.
- [ ] **(FASE 1, S21)** Tolerância zero passa a valer **também para conta corrente** (muda comportamento em prod). Confirmar com Laio/Galhardo antes de implementar. Período Omie ainda expande as bordas ou não?
- [ ] **(FASE 2, S24)** Aprovar a **quebra do invariante "Omie read-only"** para o fluxo de lançamento. Qual endpoint/campos exatos de `IncluirContaPagar`? Como garantir idempotência? _(a confirmar contra Omie real)_
- [ ] **(FASE 4, S28)** Integração Pluggy **interna vs Cubos** (proposta Arthur Souza, 16/06). Cobertura de Sicredi/BNB/Cora nos conectores.
- [ ] **(FASE 4, S30)** Primeiro endpoint **público** (webhook) — modelo de verificação de assinatura e rate limit de borda.
- [ ] **(FASE 5, S34)** Onde vive o campo de **departamento/rateio** na response Omie? Precisa `ListarDepartamentos`? _(bloqueia o check `sem_departamento`)_
- [ ] **(FASE 5, S37)** Slack App (bot token) vs Incoming Webhook; provedor de email (SES/SendGrid/SMTP Google); envio ao cliente automático ou com aprovação humana.
- [ ] **(FASE 5, S38)** Persona supervisor: role nova ou reuso de `admin`/`manager` com escopo.
- [ ] **(transversal)** Chave Anthropic com budget de longo prazo; credencial Omie real para validar campos de escrita e departamento (já em PLANO §13).

---

## Anexo A — mapa do antigo S20–S27 → FASE 5

O [PLANO_S20_AUDITORIA_CONTINUA.md](PLANO_S20_AUDITORIA_CONTINUA.md) está **superseded**. Seu conteúdo foi redistribuído assim:

| Antigo (PLANO_S20)                                           | Vira                                                                  | Mudança principal                                                                 |
| ------------------------------------------------------------ | --------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| S20 — Perfil de auditoria **+ memória/contexto por cliente** | **S26/S27** (glossário, FASE 3) + **S32** (perfil de rotinas, FASE 5) | Dividido: glossário/contexto sobe para FASE 3; perfil de cadências fica na FASE 5 |
| S21 — Motor de Audit Run ⭐                                  | **S33**                                                               | Sem ARQ — run roda em BackgroundTasks/Job; cache L1                               |
| S22 — Checks determinísticos                                 | **S34**                                                               | Igual; `sem_departamento` ainda depende de confirmar campo Omie                   |
| S23 — Tempestividade                                         | **S35**                                                               | Igual                                                                             |
| S24 — Recorrências/lembretes                                 | **S36**                                                               | Lembrete via Notifier (S37)                                                       |
| S25 — Rotinas agendadas                                      | **S37**                                                               | **Cloud Scheduler → Cloud Run Job** (não ARQ cron)                                |
| S26 — Notificações Slack/email                               | **S37**                                                               | Mesclado com rotinas; Notifier plugável                                           |
| S27 — Frontend supervisor                                    | **S38**                                                               | Igual                                                                             |

> Manter o doc antigo para a **rastreabilidade transcrito→sessão** (tabela §2 dele) e o detalhamento de modelo de dados, que continuam servindo de material de origem para a FASE 5.

---

_Documento vivo — atualizar ao fechar cada sessão e ao validar as decisões em aberto com os stakeholders._
