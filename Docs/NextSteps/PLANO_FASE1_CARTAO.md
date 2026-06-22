# Plano de Implementação — FASE 1: Conciliação de Faturas de Cartão

> **Versão 1.0 — 18/06/2026.** Plano de execução das 9 tarefas da FASE 1 (ClickUp),
> derivado do PRD [Docs/NextSteps/PRD - Próximos Passos](PRD%20-%20Pr%C3%B3ximos%20Passos-20260615173056.md)
> e detalhado contra o **código atual** (refs `arquivo:linha` verificadas em 18/06).
>
> **Status:** FASE 0 ✅ merged. FASE 1 em andamento numa branch de integração `feat/fase1-cartao` (1 PR por task pra ela; merge único na `main` no fim) — **GERAL 1.1 ✅**, **BACK 1.2 ✅**, **BACK 1.3 ✅**, **BACK 1.6 ✅**, **BACK 1.7 ✅**, **FRONT 1.4 ✅**, **FRONT 1.8 ✅**, **BACK 1.9 ✅** (8/9 feitas). Falta só **BACK 1.5** (prompt — precisa de **fatura de cartão real**). Depois: PR final integração→main (regressão CC + tirar a branch dos triggers do ci.yml).
>
> **Como usar:** cada tarefa abaixo é autocontida (objetivo, arquivos reais, passos, DoD = checklist do ClickUp, dependências). Faz-se **uma por vez**. Este doc existe pra qualquer sessão retomar sem depender do chat. Antes de iniciar uma tarefa, releia [§ Riscos críticos](#riscos-críticos) e o bloco da tarefa.
>
> **Tarefas (ClickUp):** `[GERAL 1.1]` validar ListarExtrato (Galhardo) · `[BACK 1.2]` migration ENUM+seed · `[BACK 1.3]` contas cartão no cache · `[FRONT 1.4]` tela de upload · `[BACK 1.5]` prompt Claude · `[BACK 1.6]` tolerância zero · `[BACK 1.7]` cruzamento · `[FRONT 1.8]` tela de revisão · `[BACK 1.9]` export Excel.

---

## Riscos críticos

### ✅ #1 — RESOLVIDO (18/06): `CR` = Cartão de Crédito (confirmado com dado real da Austral)

As tasks **[GERAL 1.1]** e **[BACK 1.3]** assumiam `tipo = "CA"` para cartão. **Estava errado.** Validado contra a resposta real do Omie (cache das 21 contas da Austral, cliente que tem cartões cadastrados):

- **`CR` = Cartão de Crédito** — as 6 contas `CR` da Austral são bandeiras: `ELO - AUSTRAL INSTALACAO`, `VISA - ESTER SILVA CARDIM`, etc.
- **`CA` = Conta Aplicação (investimento)** — `Bradesco - APLICACAO CDB`, `Sicredi - Dividendos`, `Banco do Nordeste - Aplicação`.
- `CC` = Conta Corrente (13) · `CX` = Caixinha (2).

Bate com o código atual e a auditoria M-1 ([omie_account_cache.py:37-44](../../apps/api/app/db/models/omie_account_cache.py#L37-L44), [integrations/omie/schemas.py:33-44](../../apps/api/app/integrations/omie/schemas.py#L33-L44)). **Decisão cravada: `tipo == 'CR'` → `account_type = 'credit_card'`; o resto → `'checking'`.** Usar `CA` reintroduziria o bug M-1 — **não usar**.

Galhardo confirmou (Slack 18/06): cartão _"fica no mesmo bloco"_ e _"tem os mesmos lançamentos de conta corrente"_ → `ListarContasCorrentes` devolve as contas `CR` na mesma resposta e `ListarExtrato` funciona com os mesmos campos. **GERAL 1.1 itens 1 e 2: confirmados.** Item 3 (`IncluirContaPagar` aparece no extrato) fica para validar live na **FASE 2** — não bloqueia a FASE 1.

> Nota: um cliente pode ter **vários** cartões (Austral tem 6 contas `CR` — ELO/VISA de titulares diferentes). Cada cartão é uma conta a conciliar separadamente. O cache já guarda as contas `CR` (não precisa "incluir" — ver BACK 1.3).

### ✅ #2 — IMPLEMENTADO (19/06, BACK 1.6): tolerância zero também na **conta corrente**

A regra de matching de data agora é **fixa** para CC **e** cartão (`DATE_DIVERGENCE_RANGE=3`, não mais `date_tolerance_days`). ⚠️ **Continua sendo mudança de comportamento em prod para a CC** — só "vale" quando a branch de integração for mergeada na `main`. Mitigado com teste de regressão de CC no job (exato/divergente/sem match) + CLAUDE.md §5.2/§5.3 atualizado. Regra (idêntica p/ CC e cartão):

| Condição                                                                    | `situation`                  | Anomalia                      |
| --------------------------------------------------------------------------- | ---------------------------- | ----------------------------- |
| valor bate (`\|diff\| ≤ 0,01`) **e data igual**                             | `conciliado`                 | —                             |
| valor bate **e data diverge em ≤ 3 dias** (`DATE_DIVERGENCE_RANGE=3`, fixo) | `conciliado_data_divergente` | `wrong_date` (auto)           |
| valor não bate com nenhum candidato                                         | `sem_omie`                   | `missing_in_omie` (já existe) |

Isso é **mudança de comportamento em prod para CC** (decisão do PRD/Laio: "extrato bancário é tolerância zero"). **DoD obrigatório:** teste de regressão de conta corrente. Atualizar CLAUDE.md §5.2/§5.3 (regra de tolerância) no PR que aterrissar a 1.6/1.7.

---

## Deltas de modelo de dados (o que muda no schema)

1. **`reconciliation_file_entries.situation`** ([reconciliation_file_entry.py:39-44, 97-98](../../apps/api/app/db/models/reconciliation_file_entry.py#L39-L98)) — hoje `String(20)` + enum app-level (`sem_omie`/`conciliado`/`ignorado`). Adicionar `conciliado_data_divergente` (**26 chars > 20** → **alargar a coluna p/ `String(30)`**; não é ENUM nativo PG, então não há `ALTER TYPE`). _(BACK 1.2)_
2. **`reconciliation_sessions.account_type`** — coluna **nova** (`String`, `'checking'`|`'credit_card'`, default `'checking'` p/ linhas existentes — não-destrutivo). Não existe hoje ([reconciliation_session.py](../../apps/api/app/db/models/reconciliation_session.py)). _(BACK 1.3)_
3. **`reconciliation_sessions.date_tolerance_days`** — **mantém a coluna** (sem migration destrutiva), mas novas sessões gravam **0**; request deixa de aceitar o campo. _(BACK 1.6)_
4. **`anomaly_types`** — inserir `wrong_date` (severity `moderate`, idempotente). Hoje o catálogo é semeado em [seed_dev.py:52+](../../apps/api/scripts/seed_dev.py#L52); prod recebe via **migration de dados** (o Cloud Run Job `migrate` roda `alembic upgrade head`). _(BACK 1.2)_

`omie_accounts_cache.account_type` **já é `String(10)`** e guarda o código cru do Omie (CC/CR/CA/…) — não precisa de migration ([omie_account_cache.py:62](../../apps/api/app/db/models/omie_account_cache.py#L62)).

---

## Dependências e ordem de execução

```
GERAL 1.1 ✅ RESOLVIDO (CR=cartão) → mapeamento de 1.3 destravado. Falta só: uma FATURA real de cartão p/ validar 1.5/1.7 live (a Austral serve de exemplo de contas CR).

BACK 1.2 (ENUM/seed) ──► BACK 1.6 (tolerância) ──► BACK 1.7 (cruzamento) ──► FRONT 1.8 (revisão)
                                                              └──► BACK 1.9 (export)
BACK 1.3 (account_type) ──► FRONT 1.4 (upload)   e   ──► BACK 1.7
BACK 1.5 (prompt) ── (quase independente; valida com fatura real)
```

**Ordem recomendada** — ⚠️ a coluna "Bloqueada por Galhardo" abaixo é a **análise inicial**; com a **GERAL 1.1 resolvida**, veja a ordem atualizada na **nota após a tabela**.

| Ordem | Task          | Bloqueada por Galhardo (1.1)? | Por quê agora                                                                             |
| ----- | ------------- | ----------------------------- | ----------------------------------------------------------------------------------------- |
| 1     | **BACK 1.2**  | ❌ não                        | Fundação (situation + anomalia `wrong_date`).                                             |
| 2     | **BACK 1.6**  | ❌ não                        | Comportamento de tolerância (CC+CA).                                                      |
| 3     | **BACK 1.7**  | 🟡 parcial                    | Algoritmo roda já p/ CC e em testes sintéticos p/ CA; **validação live CA** espera 1.1.   |
| 4     | **BACK 1.3**  | 🟡 parcial                    | Plumbing (coluna+endpoint+cache) livre; **mapeamento `tipo`→`credit_card`** gated em 1.1. |
| 5     | **BACK 1.5**  | 🟡 parcial                    | Prompt livre; **marcar concluída** exige fatura real (1.1/cliente real).                  |
| 6     | **FRONT 1.4** | 🟡 parcial                    | Depende de 1.3; rótulo "(Cartão)" depende do código confirmado em 1.1.                    |
| 7     | **FRONT 1.8** | ❌ não (após 1.7)             | UI sobre dados que 1.7 já produz (testável sintético).                                    |
| 8     | **BACK 1.9**  | ❌ não (após 1.7)             | Export sobre dados que 1.7 já produz.                                                     |
| —     | **GERAL 1.1** | —                             | Externa (Galhardo). Destrava os 🟡 acima.                                                 |

> **Atualizado 18/06 — GERAL 1.1 resolvida (`CR` = cartão):** nada mais está gated por Galhardo. A FASE 1 inteira pode ser implementada; só a **BACK 1.5** precisa de uma fatura de cartão real pra fechar a validação. **Ordem sugerida:** BACK 1.2 ✅ → **1.6 → 1.7 → 1.3 → FRONT 1.4 → FRONT 1.8 → 1.9** (1.5 quando a fatura chegar).

---

## Tarefas

### [GERAL 1.1] Validar ListarExtrato para contas de cartão com Galhardo ✅ resolvido (18/06)

**Objetivo:** confirmar contra Omie real, **antes** do cruzamento de cartão (BACK 1.7), que `ListarExtrato` funciona para contas de cartão e quais campos/formato retorna.
**O que validar (ClickUp):**

- `ListarExtrato` aceita o `nCodCC` de uma conta de cartão (hipótese: trata como qualquer conta) → confirmar comportamento real.
- Campos retornados p/ cartão são os mesmos de CC (`nCodLanc`, `cNatureza`, `dDtLanc`, `nValorLanc`, `cCateg`, `cFornecedor`) → confirmar presença e formato.
- Lançamentos criados via `IncluirContaPagar` aparecem no `ListarExtrato` da conta cartão → confirmar (é a base do cruzamento da FASE 2).

**✅ Resultado (18/06):** Galhardo confirmou no Slack — cartão _"fica no mesmo bloco"_ e _"tem os mesmos lançamentos de conta corrente"_ (itens 1 e 2 ok). E o **risco #1 foi resolvido com dado real**: nas 21 contas da Austral, cartão = `tipo` **`CR`** (contas ELO/VISA), `CA` = aplicação. **Mapeamento cravado: `CR` → `credit_card`.** Item 3 (`IncluirContaPagar` aparece no extrato) → validar live na **FASE 2**; não bloqueia a FASE 1. _Pendente (opcional): anexar no card o JSON real de `ListarExtrato` de uma conta `CR` da Austral._

---

### [BACK 1.2] Migration de schema — novo valor de situação + seed da anomalia ❌ não bloqueada

**Objetivo:** habilitar `conciliado_data_divergente` e a anomalia `wrong_date`.
**Arquivos:** [reconciliation_file_entry.py](../../apps/api/app/db/models/reconciliation_file_entry.py) (enum+coluna) · nova migration em `apps/api/alembic/versions/` · [seed_dev.py:52+](../../apps/api/scripts/seed_dev.py#L52) · [anomaly_type.py](../../apps/api/app/db/models/anomaly_type.py).
**Passos:**

1. `FileEntrySituation`: adicionar `CONCILIADO_DATA_DIVERGENTE = "conciliado_data_divergente"`.
2. Migration Alembic: **alargar** `reconciliation_file_entries.situation` de `String(20)` → `String(30)` (26 chars não cabem em 20!). Não-destrutivo.
3. Mesma migration (ou outra de dados): **INSERT idempotente** em `anomaly_types` do `wrong_date` (`name="Data do lançamento diverge do extrato ou fatura"`, `description="O valor do lançamento bate com o arquivo enviado, mas a data registrada no Omie é diferente da data no arquivo. Pode indicar erro de lançamento manual ou ajuste automático de data para dia útil."`, `severity="moderate"`, `active=true`). `ON CONFLICT (code) DO NOTHING` ou checar existência.
4. Espelhar o `wrong_date` em `seed_dev.py` (dev/local).
   **DoD (ClickUp):** coluna aceita os 4 valores; migration não afeta registros existentes; `wrong_date` inserido com os campos exatos; inserção idempotente (não falha se já existir).
   **Notas:** a task chama de "ENUM" mas é `String` + enum Python — o "ENUM" real é só alargar a coluna e adicionar o valor no enum. CI verde + migration reversível (`downgrade` volta p/ `String(20)`? só se não houver linhas com o valor novo — documentar).

---

### [BACK 1.6] Hardcodar tolerância zero e remover campo do backend ✅ feito (19/06)

**✅ Resultado (19/06) — escopo Opção A (engine genérica completa, decidido com o usuário):** a 1.6 absorveu a geração de `conciliado_data_divergente` + `wrong_date` (antes pensada p/ a 1.7), pra não deixar estado intermediário regredido e tornar o teste de regressão da CC significativo. Implementado: `DATE_DIVERGENCE_RANGE=3` no matcher; `MatchResult.days_diff_by_file_id`; `job.py` classifica exato→`conciliado`, 1-3d→`conciliado_data_divergente` (+ `wrong_date` via `create_structural_anomalies(divergent_file_entry_ids=...)`), >3d→`sem_omie`; `apply_matches` recebe a `situation`; `date_tolerance_days` removido do request (ignorado), coluna mantida e novas sessões gravam 0; range fixo também na janela Omie do `job.py`, `omie_data/service.py` (revisão) e `export/service.py`. CLAUDE.md §5.2/§5.3 atualizado. Testes: regressão CC no job (exato/divergente/sem match) + matcher days_diff + create ignora campo. Gate verde. **➡️ Sobra p/ a BACK 1.7:** só o que é específico de cartão (fetch via `ListarExtrato` da conta cartão, parcelas individualizadas, omie_entries do cartão, contexto "data arquivo x data Omie" na anomalia) — a engine de classificação já está pronta e é reusada.

**Objetivo:** fixar a regra de data no backend (exato → `conciliado`; ≤3d → `conciliado_data_divergente`) e remover `date_tolerance_days` da request.
**Arquivos:** [matcher.py](../../apps/api/app/modules/reconciliations/processing/matcher.py) · [reconciliations/schemas.py:103](../../apps/api/app/modules/reconciliations/schemas.py#L103) · [service.py (create_session_with_entries)](../../apps/api/app/modules/reconciliations/service.py#L82) · [job.py (expansão de período)](../../apps/api/app/modules/reconciliations/processing/job.py#L199-L217) · [reconciliation_session.py:99](../../apps/api/app/db/models/reconciliation_session.py#L99).
**Passos:**

1. Constante `DATE_DIVERGENCE_RANGE = 3` no matcher (fixa, não exposta).
2. Remover `date_tolerance_days` do request schema (`CreateReconciliationRequest`); se enviado, **ignorar** (não falhar).
3. `create_session_with_entries`: gravar `date_tolerance_days = 0` em novas sessões (coluna mantida; default do model pode ir p/ 0).
4. `job.py`: a janela Omie buscada passa a expandir por `DATE_DIVERGENCE_RANGE` (3), não por `tolerance_days`.
   **DoD (ClickUp):** backend ignora `date_tolerance_days` no body; coluna recebe 0 em novas sessões (sem migration destrutiva); matching usa data exata p/ `conciliado`; range de 3 dias fixo no código e não exposto.
   **Notas:** o miolo da lógica de `conciliado_data_divergente` é a BACK 1.7 — aqui é a parametrização/constante + remoção do campo. ⚠️ muda CC (risco #2).

---

### [BACK 1.7] Lógica de cruzamento para faturas de cartão ✅ feito (19/06)

**✅ Resultado (19/06):** a engine de matching/classificação já vinha da 1.6 (mesmo algoritmo p/ `credit_card` e `checking` — o `job.py` não ramifica por tipo). A 1.7 fechou o que faltava: **contexto da anomalia `wrong_date`** (`context_encrypted` = `"Data arquivo: X · Data Omie: Y"`, cifrado AES-256 via helper `_build_wrong_date_anomalies`; o job monta o `DivergentMatch` com as datas e passa a `encryption_key`). Confirmado por leitura que `fetch_realized`/`fetch_pending` são agnósticos a tipo de conta (cartão = só outro `nCodCC` via `ListarExtrato`), então itens 1 e 5 já funcionavam. Testes novos: sessão `credit_card` ponta a ponta (exato/divergente/3 parcelas sem*omie/omie sem correspondente → omie_entries) + asserção do contexto decifrado. Gate verde (527 pytest). \_Validação com fatura real fica na BACK 1.5 (prompt) quando a Austral mandar.*

**Objetivo:** reusar o matcher para gerar `conciliado_data_divergente` + anomalia `wrong_date`; mesmo algoritmo p/ CC e cartão.
**Arquivos:** [matcher.py](../../apps/api/app/modules/reconciliations/processing/matcher.py) · [processing/anomalies.py](../../apps/api/app/modules/reconciliations/processing/anomalies.py) · [processing/omie_fetch.py](../../apps/api/app/modules/reconciliations/processing/omie_fetch.py) · [job.py](../../apps/api/app/modules/reconciliations/processing/job.py).
**Regras (idênticas CC e CA):** ver tabela em [Riscos #2](#riscos-críticos). Omie via `ListarExtrato` (`financas/extrato`, `nCodCC`=conta, período = mês de referência). Contexto da anomalia: `"Data arquivo: {X} · Data Omie: {Y}"`, `detected_by='ai'`.
**Particularidade de parcelas:** cada parcela da fatura é cruzada **individualmente**. Se o Omie tem 1 lançamento pelo total da compra parcelada e a fatura tem N parcelas, as N parcelas ficam `sem_omie` — o sistema **não agrupa**; analista revisa.
**DoD (ClickUp):** `credit_card` e `checking` usam o mesmo algoritmo; `conciliado_data_divergente` correto p/ ≤3d; `wrong_date` gerada p/ cada linha divergente; contexto com data arquivo + data Omie lado a lado; lançamentos Omie da conta cartão sem correspondente vão p/ `reconciliation_omie_entries`.
**Dependências:** BACK 1.2, 1.6, 1.3. **GERAL 1.1** p/ validar o caminho CA contra Omie real (o algoritmo em si é testável com `respx`/fixtures p/ CC e CA antes disso).

---

### [BACK 1.3] Incluir contas de cartão no cache e no endpoint de contas ✅ feito (19/06)

**✅ Resultado (19/06):** o cache já incluía todos os tipos (`listar_contas_correntes` não filtra — confirmado); `BankAccountResponse.account_type` já expunha o código Omie. Adicionados: coluna `reconciliation_sessions.account_type` (migration `b2f7c4a9d318`, server_default `'checking'`, não-destrutiva — backfill verificado no dev + round-trip), enum `SessionAccountType`, helper `session_account_type_from_omie_tipo` (**`CR`→`credit_card`; resto, incl. `CA` e None → `checking`** — anti-M-1) e derivação em `create_session_with_entries` a partir do `tipo` cacheado (server-side, não do palpite da IA). Corrigido docstring enganoso em `client.py` (`CA`=cartão → `CR`=cartão). Testes: 4 de integração (CR/CC/CA/uncached) + 7 unit do mapeamento. Gate verde (519 pytest).

**Objetivo:** o cache e o endpoint de contas passam a incluir contas de cartão; a sessão grava `account_type`.
**Arquivos:** [accounts_cache.py](../../apps/api/app/modules/clients/accounts_cache.py) · [omie_account_cache.py](../../apps/api/app/db/models/omie_account_cache.py) (já tem `account_type`) · [clients/schemas.py (BankAccountResponse)](../../apps/api/app/modules/clients/schemas.py#L121-L154) · endpoint `GET /api/v1/clients/{id}` · **nova coluna** `account_type` em [reconciliation_session.py](../../apps/api/app/db/models/reconciliation_session.py) + migration · [service.create_session_with_entries](../../apps/api/app/modules/reconciliations/service.py#L82).
**Passos:**

1. Cache: garantir que `ListarContasCorrentes` inclui todos os tipos (hoje o filtro do form não filtra por tipo — confirmar que CR/CA entram no cache). `account_type` já guarda o código cru.
2. `BankAccountResponse` já expõe `account_type` (código Omie) — confirmar que o endpoint devolve contas de cartão também.
3. Migration: adicionar `account_type String` em `reconciliation_sessions` (default `'checking'`, não-destrutivo).
4. **Mapeamento `tipo` Omie → `account_type` (CONFIRMADO):** num único helper, `tipo == 'CR' → 'credit_card'`, senão `'checking'`. (`CR` = cartão confirmado com dado real da Austral — risco #1 resolvido; **não usar `CA`** = aplicação.)
5. `create_session_with_entries`: setar `account_type` da sessão a partir do tipo da conta selecionada.
   **DoD (ClickUp):** cache guarda CC e cartão; `account_type` reflete o `tipo` Omie; `GET /clients/{id}` devolve ambos com `account_type` exposto; sessão com conta cartão → `account_type='credit_card'`; conta CC → `'checking'` (sem regressão).
   **✅ Resolvido (risco #1):** a task escreve `CA`, mas o `tipo` real do cartão é `CR` (confirmado nas contas ELO/VISA da Austral; `CA` = aplicação). Usar `CR`.

---

### [BACK 1.5] Adaptar prompt da Claude API para faturas de cartão 🟡 validação com fatura real

**Objetivo:** o prompt de extração trata as particularidades da fatura.
**Arquivos:** [anthropic/prompts.py:19-72](../../apps/api/app/integrations/anthropic/prompts.py#L19-L72) · [anthropic/tools.py:24-107](../../apps/api/app/integrations/anthropic/tools.py#L24-L107) · [anthropic/schemas.py:48-92](../../apps/api/app/integrations/anthropic/schemas.py#L48-L92).
**Regras a cobrir:** parcelas individualizadas (3x → 3 objetos, data real de cada, valor unitário; padrões `1/3`,`2/3`); estornos = crédito (amount positivo); encargos (juros/IOF/multa) = transações separadas com descrição exata; pagamento da fatura **não** incluir (pertence ao extrato CC); sinal (compras/encargos negativos, créditos positivos).
**DoD (ClickUp):** `account_type='credit_card'` quando fatura; 3x → 3 objetos com data/valor unitário; estornos positivos; encargos separados; pagamento de fatura fora; **validar com fatura real de cliente ativo antes de concluir**.
**Notas:** a infra já suporta `account_type` (`checking`/`credit_card`) no tool/prompt — é refino do guia de extração. A validação final precisa de fatura real (coordenar com 1.1/Galhardo).

---

### [FRONT 1.4] Adaptar tela de upload para faturas de cartão ✅ feito (20/06)

**✅ Resultado (20/06):** detecção de cartão por **`CR`** via helper `isCreditCardAccount` em `lib/api/clients.ts` (anti-M-1; `formatAccountLabel` refatorado p/ usá-lo — o sufixo "(Cartão)" já existia). Form: `isCardSelected` (do `selectedAccount`) dirige label `Arquivo do Extrato`↔`Arquivo da Fatura`, texto auxiliar de fatura, nota `CardInvoiceNote`, e badge azul "Cartão de Crédito" no header. Prévia (`parse-preview.tsx`): props `isCard`/`accountName` → título "Prévia da fatura — {conta}" + legenda compras/estornos. **Tolerância removida** do schema zod, do form e do `CreateReconciliationPayload` (backend ignora desde 1.6). Testes: `isCreditCardAccount` (unit), schema sem tolerância, `ParsePreview` cartão/CC, render default do form (tolerância removida + label CC; `ui/select` mockado pois Radix não resolve no vitest). Gate verde (lint, type-check, 16 testes novos). _Modo-cartão dinâmico do form (depende do Radix Select) → verificação manual/E2E._

**Objetivo:** o formulário de nova conciliação muda dinamicamente ao selecionar conta de cartão.
**Arquivos:** [new-reconciliation-form.tsx](../../apps/web/src/components/features/reconciliations/new-reconciliation-form.tsx) (rótulos, `formatAccountLabel` ~594-603, campo tolerância 433-480) · [lib/validation/reconciliations.ts:17-19](../../apps/web/src/lib/validation/reconciliations.ts#L17-L19) (zod tolerância) · tela de prévia do parsing.
**Passos / DoD (ClickUp):**

- Select: contas de cartão com sufixo `"(Cartão)"`; CC sem sufixo. _(o label de cartão já existe p/ `CR`; confirmar o código em 1.1)_
- Ao selecionar cartão: label `"Arquivo do Extrato"`→`"Arquivo da Fatura"`; texto auxiliar "PDF ou XLS da fatura… Máx 20MB"; nota "Inclua somente o arquivo da fatura… O pagamento aparecerá no extrato da conta corrente — não inclua aqui."; badge azul "Cartão de Crédito" no header.
- Prévia (cartão): título "Prévia da fatura — {conta}"; legenda "Valores negativos = compras · positivos = estornos/créditos"; 5 primeiras linhas Data/Descrição/Valor.
- **Remover campo "Tolerância de Data"** do form p/ ambos os tipos (coordena com BACK 1.6); nenhum dropdown/slider/input de tolerância.
  **Dependência:** BACK 1.3 (account_type exposto no endpoint de contas).

---

### [FRONT 1.8] Adaptações na tela de revisão para faturas de cartão ✅ feito (20/06)

**✅ Resultado (20/06):** **backend** expõe `account_type` no `SessionDetailPayload` (+ serviço) → o `review-screen` deriva `isCard` e passa pro header/abas. **Header** (`review-header.tsx`): badge de tipo (Conta Corrente cinza / Cartão azul) + título "Conciliação · {Cartão|Conta Corrente} · {conta} · {Mês/Ano}". **Movimentações** (`movements-tab.tsx`): filtro de tipo vira Compras/Estornos no cartão; linha `conciliado_data_divergente` → badge laranja "⚠ Data divergente" (`situation-badge.tsx`) com tooltip "Data no arquivo: X · Data no Omie: Y" (data Omie do lançamento já no lookup batched). **Resumo** (`summary-tab.tsx`): no cartão, Total de compras/estornos/encargos (IOF/juros/multa por descrição, helper `isChargeDescription`) + Saldo da fatura (`balance_end_file`). Anomalia `wrong_date` já aparece linkada na aba Anomalias (listagem genérica por `related_file_entry`). Testes: situation-badge, review-header, isChargeDescription + backend expõe account*type. Gate verde (ruff/mypy backend; lint/type-check + 16 testes front). \_Modo-cartão das abas (Radix Select) → manual/E2E.*

**Objetivo:** ajustes visuais/funcionais na tela de revisão p/ contexto de cartão (mesma estrutura).
**Arquivos:** [review/movements-tab.tsx:215-229](../../apps/web/src/components/features/reconciliations/review/movements-tab.tsx#L215-L229) (filtros) · header da revisão · aba de anomalias · aba resumo.
**Passos / DoD (ClickUp):**

- Header: badge tipo de conta (Conta Corrente cinza / Cartão azul); título "Conciliação · Cartão · {conta} · {Mês/Ano}".
- Aba Movimentações: filtro com labels **"Compras (amount<0)" / "Estornos (amount>0)"** em vez de Débitos/Créditos; linhas `conciliado_data_divergente` com badge laranja "⚠ Data divergente" + tooltip/expansão "Data no arquivo: {X} · Data no Omie: {Y}"; anomalia `wrong_date` linkada à linha na aba Anomalias.
- Aba Resumo: indicadores Total de compras / estornos / encargos (IOF/juros/multa por descrição) / Saldo da fatura.
  **Dependência:** BACK 1.7 (produz `conciliado_data_divergente` + `wrong_date`), BACK 1.3 (account_type).

---

### [BACK 1.9] Adaptar exportação Excel para faturas de cartão ✅ feito (22/06)

**✅ Resultado (22/06):** `ExportPayload.is_card` (do `session.account_type`) + `FileEntryRow.omie_date` (data do lançamento Omie casado, do cache que o service já consulta). **Aba 1** (`workbook._build_sheet1_summary`): título tipado — cartão "CONCILIAÇÃO DE FATURA — CARTÃO | {conta} | {Mês/Ano}", CC "CONCILIAÇÃO BANCÁRIA — {banco} | {conta} | {Mês/Ano}" (decisão do usuário: aplicar o formato tipado nos dois; o genérico "Relatório de Conciliação" some). **Aba 2** (`_build_sheet2_movimentacao`): no cartão, coluna "Data Omie" ao lado de "Data"; `conciliado_data_divergente` → data Omie + célula laranja (`FILL_DATA_DIVERGENTE`); conciliado sem divergência → vazia; CC → sem coluna. Testes: título cartão/CC + Aba 2 cartão (coluna/laranja/vazia) e CC (sem coluna); teste do A1 atualizado. Sem migration. 290 testes unit (43 export). _Validação com fatura real fica na BACK 1.5._

**Objetivo:** o Excel reflete que é fatura de cartão (título diferente + coluna de data Omie p/ linhas divergentes).
**Arquivos:** módulo de export `apps/api/app/modules/reconciliations/export/` — **localizar no código ao iniciar** os pontos exatos (nomes de abas, cabeçalhos, colunas; openpyxl).
**Passos / DoD (ClickUp):**

- Aba 2 (Movimentação x Lançamento): p/ cartão, adicionar coluna **"Data Omie"** ao lado de "Data"; linhas `conciliado_data_divergente` → preencher Data Omie + célula fundo laranja claro; `conciliado` sem divergência → Data Omie vazia; **CC → coluna não aparece** (sem mudança de layout).
- Aba 1 (Resumo): cartão → cabeçalho "CONCILIAÇÃO DE FATURA — {CARTÃO} | {conta} | {Mês/Ano}"; CC → mantém "CONCILIAÇÃO BANCÁRIA — {BANCO} | {conta} | {Mês/Ano}".
  **Dependência:** BACK 1.7 (dados divergentes), BACK 1.3 (account_type).

---

## Decisões em aberto (confirmar antes de cravar)

- [x] **(risco #1) Código Omie do cartão:** **RESOLVIDO (18/06)** — é `CR` (dado real da Austral: contas ELO/VISA = `CR`; `CA` = aplicação). Mapeamento: `CR`→`credit_card`.
- [ ] **(risco #2) Tolerância zero na conta corrente** muda comportamento em prod — confirmar que está aprovado (PRD diz que sim; tasks 1.6/1.7 codificam). DoD: regressão de CC + atualizar CLAUDE.md §5.
- [ ] **Migration `downgrade` da situation** (`String(30)`→`String(20)`) só é segura sem linhas com o valor novo — documentar como irreversível na prática após uso.
- [ ] **Encargos no resumo (FRONT 1.8):** "identificados pela descrição" (IOF/juros/multa) — definir heurística (palavras-chave) e onde calcular (front a partir das linhas, ou backend).

---

## Como retomar (qualquer sessão)

1. Ler [§ Riscos críticos](#riscos-críticos) e a tabela de ordem.
2. Pegar a próxima task não-feita na ordem recomendada que **não** esteja bloqueada por 1.1 (se 1.1 ainda aberta).
3. Abrir os arquivos listados na task, reler o contrato, implementar, rodar o gate local (CLAUDE.md §7), abrir PR por task (ou agrupado por afinidade — alinhar com o usuário).
4. Atualizar este doc (checkbox/▶) e o CLAUDE.md quando uma regra inviolável mudar (tolerância §5).

_Documento vivo — uma task por vez; o usuário cronometra cada uma._
