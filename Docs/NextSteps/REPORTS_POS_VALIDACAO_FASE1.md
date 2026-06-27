# Reports pós-validação — FASE 1 (cartão) + conta corrente/aplicação

> **Documento vivo.** Cada report = bug/observação encontrada na validação da FASE 1
> (jun/2026), com três blocos fixos: **Report** (o que foi observado), **Diagnóstico**
> (causa raiz verificada — nunca chute; cita log/print/código) e **Opções de fix**
> (com a decisão, quando houver). Atualizado conforme os reports chegam (1 por vez).
>
> Contexto de ambiente: os reports vêm do **dev no Cloud Run** (GCP `liberdade-assessoria`,
> serviço `auditoria-api-dev`, `southamerica-east1`). Logs em `textPayload` (ConsoleRenderer,
> não JSON — `is_production=False` no dev). Busca: `gcloud logging read 'resource.type="cloud_run_revision" AND "<texto>"' --project=liberdade-assessoria`.

## Legenda de status

- 🔴 **Aberto** — diagnosticado, fix **não decidido**.
- 🟡 **Decidido** — fix escolhido, **não implementado**.
- 🟢 **Resolvido** — implementado + verificado (link do PR/commit).

## Índice

| #   | Título                                            | Causa raiz                                                      | Status    |
| --- | ------------------------------------------------- | --------------------------------------------------------------- | --------- |
| 1   | Resgate de conta aplicação vira "sem Omie"        | Tolerância de valor R$ 0,01 não absorve o rendimento do resgate | 🔴 Aberto |
| 2   | Qualificação (IA) marca APLICACAO como incoerente | IA não sabe que é conta aplicação (semântica entrada/saída ⇄)   | 🔴 Aberto |

## ⭐ Causa raiz comum (reports #1 e #2)

Os dois reports são sintomas da **mesma raiz-tema: o sistema não trata "Conta Aplicação" (Omie
`tipo=CA`) como um tipo de conta próprio.** Hoje `session_account_type_from_omie_tipo` mapeia só
`CR → credit_card`; **`CA` cai em `checking`** — ou seja, a aplicação é tratada como conta corrente,
mas a semântica dela é diferente:

- **Sinal invertido:** numa aplicação, **APLICACAO = entrada (+)** e **RESGATE = saída (−)** (o
  oposto da conta corrente). → quebra a **qualificação** (#2).
- **Resgate carrega rendimento:** o valor do resgate no banco ≠ valor da transferência no Omie. →
  quebra o **matching** (#1).

**Implicação:** assim como _cartão_ virou a FASE 1, **conta aplicação pode merecer tratamento
próprio** (um `account_type='investment'` derivado de `tipo=CA`, com regras de matching +
qualificação específicas). Decidir o escopo com o usuário/Galhardo antes de codar — pode ser um
fix pontual (b/c de cada report) ou uma "mini-fase aplicação".

---

## Report #1 — Resgate de conta aplicação vira "sem Omie"

**Status:** 🔴 Aberto (diagnóstico confirmado; fix depende de decisão de produto + reports 2–4).

### Report

> _"@Pedro testando a conta de aplicação da Horus, identifiquei que os lançamentos de
> transferências entre contas não estão sendo considerados pela IA, por isso aparece a
> anomalia de Movimentação sem lançamento no Omie."_ — 27/06/2026

- **Cliente/conta:** Horus · `Itaú Unibanco - CDB-DI` (conta **aplicação**) · `n_cod_cc=10974894019` · Maio/2026.
- **Ambiente:** prod/dev Cloud Run (`auditoria-api-dev`). Conciliação feita 26/06 ~09–10h30 BRT.
- **Sintoma:** resultado **1 conciliado / 7 sem Omie / 6 Omie sem arquivo / 8 anomalias**.
  Todos os `RESGATE` do extrato viraram anomalia crítica "Movimentação sem lançamento no Omie".

### Diagnóstico (causa raiz — **CONFIRMADA**)

**Não é a IA, e não é falha de busca.** A IA só extrai o arquivo; o cruzamento é código
determinístico ([`processing/omie_fetch.py`](../../apps/api/app/modules/reconciliations/processing/omie_fetch.py),
[`processing/matcher.py`](../../apps/api/app/modules/reconciliations/processing/matcher.py)).
O matcher **não filtra por tipo nem por status** — todo lançamento do `ListarExtrato` entra como candidato.

**As transferências CHEGAM do Omie.** Log de prod (`omie_extrato_size`) da conciliação:

```
n_cod_cc=10974894019 · path=/api/v1/reconciliations
period_start=2026-05-03  period_end=2026-05-30
raw_count=36  summary_rows_skipped=29  item_count=7
```

→ `ListarExtrato` voltou **36 linhas → 29 de SALDO (descartadas, atualização diária de saldo da
aplicação) → 7 lançamentos reais**. A aba **"Divergências Omie"** mostra 6 desses 7: todos
**"Saída de Transferência" · Conciliado** (`Transf. Itaú Unibanco - CDB-DI >> Itaú Unibanco`).
O 7º casou (= o "1 conciliado").

**A causa é VALOR.** A data bate perfeitamente, mas o valor do extrato bancário (RESGATE) é
sempre **um pouco maior** que o da transferência no Omie:

| Data  | Arquivo (RESGATE) | Omie (Saída de Transf.) | Diferença |
| ----- | ----------------- | ----------------------- | --------- |
| 11/05 | −15.100,96        | −15.000,85              | −100,11   |
| 15/05 | −8.034,03         | −7.977,10               | −56,93    |
| 25/05 | −5.011,09         | −5.000,45               | −10,64    |
| 26/05 | −28.069,97        | −28.000,41              | −69,56    |
| 27/05 | −33.092,97        | −33.000,13              | −92,84    |

O matcher exige `|a − b| ≤ R$ 0,01` (CLAUDE.md §5.1, **regra dura, não parametrizável**) → não
pareia → cada lado vira "sem Omie" / "Omie sem arquivo".

**Por que o valor difere:** muito provavelmente **rendimento (e/ou IR) do resgate** — no resgate
do CDB o banco credita _principal + rendimento_, mas o Omie registra a transferência por outro
valor (provavelmente o principal). A diferença cresce com valor/tempo aplicado, coerente com
rendimento. _(A confirmar com o financeiro/Galhardo — o "1 conciliado" provavelmente é um resgate
sem rendimento acumulado, por isso bateu exato.)_

**Conclusão:** o sistema está tecnicamente "certo" (os valores realmente divergem), mas para
**resgate de conta aplicação** essa diferença é esperada → vira **falso-positivo**.

### Opções de fix

- **(a) Tolerância maior só para "Transferência".** Casar lançamentos de categoria _Saída/Entrada
  de Transferência_ por data + faixa/% de valor, registrando a diferença como rendimento.
  ⚠️ Hoje o `fetch_realized` **não carrega a categoria** do Omie pro matcher → mexe em fetch +
  matcher + classificação de anomalia. Afrouxar a tolerância tem que ser cirúrgico (só
  transferência), senão gera falso-match no resto.
- **(b) Transferência entre contas próprias não é anomalia crítica.** Marcar como
  "conciliado com divergência de valor/rendimento" (análogo ao `conciliado_data_divergente`, mas
  para valor) em vez de "sem Omie". **← inclinação atual.**
- **(c) Manter** e orientar conferência manual do resgate (a diferença é rendimento a contabilizar).

### Decisão

**Pendente.** Precisa de (1) alinhamento com Galhardo/produto — _como a Hologram quer que resgate
de aplicação concilie? o rendimento aparece como quê?_ — e (2) verificar se os reports 2–4
compartilham a mesma causa raiz (aplicação/transferência/rendimento) antes de desenhar o fix.
Ver **"Causa raiz comum"** no topo — #1 e #2 são a mesma raiz (conta aplicação não tratada).

---

## Report #2 — Qualificação (IA) marca APLICACAO como incoerente em conta aplicação

**Status:** 🔴 Aberto (diagnóstico confirmado; mesma raiz-tema do #1 — ver "Causa raiz comum").

### Report

> _"@Pedro Aqui diz que o lançamento de aplicação deveria ter sido registrado como saída, mas na
> verdade é uma entrada mesmo, tanto no extrato como no Omie estão corretos."_ — 27/06/2026

- **Mesma conciliação do #1:** Horus · `Itaú CDB-DI` (aplicação) · Maio/2026.
- **Lançamento:** `18/05 · APLICACAO · +R$ 207.000,00`. No extrato Itaú (CDB) é uma APLICACAO; no
  Omie é "Entrada de Transferência" +207.000,00, Conciliado. **Ambos corretos** — aplicar dinheiro
  ENTRA no CDB (positivo). _(Este foi o "1 conciliado" do #1 — casou exato porque aplicação não
  tem rendimento embutido.)_
- **Sintoma:** anomalia crítica **"Qualificação incoerente (IA)"**: _"APLICACAO (saída para
  investimento) classificada como 'Entrada de Transferência'; deveria ser saída/aplicação
  financeira."_ → **falso-positivo**.

### Diagnóstico (causa raiz — **CONFIRMADA**)

A anomalia vem da **Camada 1 da qualificação** (S19) — o check semântico via Claude em
[`qualification/semantic.py`](../../apps/api/app/modules/reconciliations/qualification/semantic.py).
O modelo recebe `(descricao_extrato, fornecedor_omie, categoria_omie, valor)` e decide
`ok | suspeita | incoerente`.

**O payload NÃO informa o tipo da conta** (`_build_user_payload` manda só descrição/fornecedor/
categoria/valor — nada diz que é conta aplicação/CDB). E o `_SYSTEM_PROMPT` assume a **perspectiva
da conta corrente**, onde "APLICACAO" = dinheiro saindo:

> "o sinal indica natureza (negativo=saída, positivo=entrada)... Categoria de receita em valor
> negativo (ou vice-versa) → incoerente."

Na **conta aplicação (CDB) a perspectiva é invertida**: APLICACAO = ENTRADA (+) e RESGATE =
SAÍDA (−). A IA vê a palavra "APLICACAO" (que associa a saída) com valor POSITIVO + categoria
"Entrada de Transferência", lê como contradição e marca **incoerente** — sendo que está tudo certo.

**Conclusão:** falso-positivo da qualificação porque a IA não sabe que a conta é de aplicação.

### Opções de fix

- **(a) Dar contexto de conta à qualificação.** Passar o tipo da conta no payload + regra no
  `_SYSTEM_PROMPT`: _"Em conta de aplicação/investimento (CDB), APLICACAO é ENTRADA (+) e RESGATE é
  SAÍDA (−); transferências entre contas próprias são coerentes."_ Requer identificar a conta como
  aplicação (Omie `tipo=CA`, já no cache). **← mais correto.**
- **(b) Pular transferência entre contas próprias da Camada 1.** Categoria "Entrada/Saída de
  Transferência" não é classificação contábil a auditar (é movimentação interna) → excluir do check.
- **(c) Não rodar qualificação em conta aplicação (CA).** Mais simples, mas perde a auditoria
  semântica do resto da conta.

### Decisão

**Pendente** — junto com o #1 (mesma raiz: conta aplicação). Provável fix combinado se virar uma
"mini-fase aplicação"; ou (a)+(b) pontuais.
