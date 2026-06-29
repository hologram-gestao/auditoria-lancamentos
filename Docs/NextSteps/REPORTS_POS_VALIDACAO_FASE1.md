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

| #   | Título                                            | Causa raiz                                                          | Status            |
| --- | ------------------------------------------------- | ------------------------------------------------------------------- | ----------------- |
| 1   | Resgate de conta aplicação vira "sem Omie"        | IA extrai o valor BRUTO; Omie usa o LÍQUIDO creditado (dif. IOF+IR) | 🔴 Aberto         |
| 2   | Qualificação (IA) marca APLICACAO como incoerente | IA não sabe que é conta aplicação (semântica entrada/saída ⇄)       | 🔴 Aberto         |
| 3   | CSV grande (Banco Inter) falha no parse           | Truncamento do output da IA (max_tokens=8192 < ~220 linhas)         | 🟢 Corrigido (PR) |
| 4   | XLSX (DM) extrai só 1 de ~20 transações           | `_xlsx_to_text` lê só ~3 linhas (openpyxl read_only + dimension)    | 🟢 Corrigido (PR) |

> **Raízes (revisado 27/06):** **#2** = semântica de "conta aplicação" (a IA não sabe que
> APLICACAO=entrada / RESGATE=saída). **#1, #3 e #4 = extração** — #1 a IA pega a coluna de valor
> errada (bruto vs líquido) no extrato de CDB; #3 trunca o _output_ por excesso de linhas; #4 lê
> _input_ incompleto do XLSX. _(O #1 era classificado junto do #2 como "conta aplicação"; a correção
> mostrou que é problema de extração.)_ Os de extração reforçam a ideia de um **parser determinístico**
> p/ CSV/XLSX estruturado + extração mais precisa de extratos com várias colunas de valor.

## ⭐ Sobre conta aplicação (reports #1 e #2)

> **Revisão 27/06:** estes dois NÃO compartilham mais a mesma causa raiz. A 1ª análise os juntou em
> "o sistema não trata Conta Aplicação como tipo próprio"; ao reexaminar, **só o #2 é semântica de
> aplicação** — o **#1 é extração** (a IA pega a coluna de valor errada no extrato do CDB). Ainda
> ambos aparecem em conta de aplicação, então valem o mesmo contexto, mas os fixes são diferentes.

- **#2 — semântica:** numa aplicação, **APLICACAO = entrada (+)** e **RESGATE = saída (−)** (o oposto
  da conta corrente); a IA da qualificação não sabe disso. Fix = dar contexto de conta à qualificação.
  _(Hoje `session_account_type_from_omie_tipo` mapeia só `CR → credit_card`; `CA` cai em `checking` —
  então a aplicação é tratada como conta corrente. Identificar `tipo=CA` ajuda neste e em futuros casos.)_
- **#1 — extração:** o valor que move a conta (e que o Omie registra) é o **líquido creditado**; a IA
  extraiu o **bruto**. Fix = extrair o valor líquido. Mais perto de #3/#4 (robustez de extração).

**Implicação:** ainda pode fazer sentido um **tratamento próprio de conta aplicação** (`tipo=CA`)
para a qualificação (#2); o #1 resolve-se melhorando a extração. Decidir escopo com o grupo/Galhardo.

---

## Report #1 — Resgate de conta aplicação vira "sem Omie"

**Status:** 🔴 Aberto. **⚠️ CORREÇÃO 27/06:** a 1ª análise atribuiu a diferença a "rendimento" —
**estava errado**. O extrato do CDB prova que a diferença é **imposto (IOF + IR)**, e a raiz real é
**a IA extrair a coluna de valor errada** (bruto em vez do líquido creditado). Ver abaixo.

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

**Por que o valor difere — é IMPOSTO (IOF + IR), NÃO rendimento.** O extrato do CDB (mandado no #2)
tem **várias colunas de valor**: `Valor(*)` (bruto, já com rendimento acumulado) · `IOF` · `IR` ·
`Valor creditado` (líquido) · `Valor principal`. A **IA extraiu o `Valor(*)` (bruto)**; o que de
fato sai pra conta corrente — e o **Omie registra — é o `Valor creditado` (líquido = bruto − IOF − IR)**:

| Data  | Valor bruto (extraído) | IOF   | IR     | Valor creditado (= Omie) | Diferença |
| ----- | ---------------------- | ----- | ------ | ------------------------ | --------- |
| 11/05 | 15.100,96              | —     | 100,11 | 15.000,85                | IR        |
| 25/05 | 5.011,09               | 9,94  | 0,70   | 5.000,45                 | IOF + IR  |
| 27/05 | 33.092,97              | 84,68 | 8,16   | 33.000,13                | IOF + IR  |

O **rendimento** (ex.: ~444,96 em 11/05 = bruto − principal 14.656,00) está embutido nos **dois**
valores — **não é a diferença**.

⚠️ **Generalização (não assumir "imposto" sempre):** a raiz é a IA **escolher a coluna de valor
errada** num extrato com múltiplas colunas (bruto vs líquido). No CDB Itaú a diferença é IOF+IR; em
**outros extratos/bancos** a diferença pode ter **outra natureza** (rendimento, tarifas, IOF, IR,
etc.). O fix correto não é codar "subtrair imposto", e sim **conciliar pelo valor líquido
efetivamente movimentado** (o que o Omie registra).

**Conclusão:** **não é lacuna de tolerância nem regra de conta aplicação** — é **extração da coluna
errada**. Se a extração pegar o valor líquido creditado, os valores batem exato e conciliam.

### Opções de fix

- **(a) Extrair o valor LÍQUIDO efetivamente movimentado.** Em extrato com várias colunas de valor
  (CDB/aplicação), usar o "valor creditado/debitado" — bate exato com o Omie, concilia sem tolerância.
  Via prompt da extração (ensinar a IA a pegar o valor que de fato move a conta) e/ou parser
  determinístico (liga com #3/#4). **← caminho mais correto.**
- **(b) Matcher tolera a diferença e a registra.** Marcar "conciliado com divergência" (análogo ao
  `conciliado_data_divergente`, mas pra valor) em vez de "sem Omie". Mais frágil — afrouxar valor é
  regra dura (§5.1) e a diferença varia por extrato; só se (a) não for viável.
- **(c) Confirmar a contabilização com o financeiro:** o IOF/IR retido e o rendimento bruto são (ou
  não) lançados no Omie? Isso decide se viram lançamentos a conciliar à parte ou se são ignorados.

### Decisão

**Pendente.** Confirmar com o grupo: (1) a conciliação do resgate é pelo **valor líquido creditado**
(o que o Omie registra)? (2) IOF/IR e rendimento têm lançamento próprio no Omie? Fix provável: **(a)
extrair o valor líquido**. _(Atenção: a diferença ser IOF+IR é específica deste CDB Itaú — em outros
extratos a diferença pode ter outra natureza; não generalizar "imposto".)_

---

## Report #2 — Qualificação (IA) marca APLICACAO como incoerente em conta aplicação

**Status:** 🔴 Aberto (diagnóstico confirmado; semântica de conta aplicação — ver "Sobre conta
aplicação". Obs.: o #1, após correção, NÃO é mais a mesma raiz).

### Report

> _"@Pedro Aqui diz que o lançamento de aplicação deveria ter sido registrado como saída, mas na
> verdade é uma entrada mesmo, tanto no extrato como no Omie estão corretos."_ — 27/06/2026

- **Mesma conciliação do #1:** Horus · `Itaú CDB-DI` (aplicação) · Maio/2026.
- **Lançamento:** `18/05 · APLICACAO · +R$ 207.000,00`. No extrato Itaú (CDB) é uma APLICACAO; no
  Omie é "Entrada de Transferência" +207.000,00, Conciliado. **Ambos corretos** — aplicar dinheiro
  ENTRA no CDB (positivo). _(Este foi o "1 conciliado" do #1 — casou exato porque a APLICACAO é só o
  principal depositado, sem IOF/IR/rendimento embutido; é o resgate que tem a diferença, não a
  aplicação.)_
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

**Pendente.** Fix = dar contexto de conta à qualificação (opção (a)+(b)). Não depende do #1 (raízes
diferentes após a correção). Confirmar com o grupo só o entendimento contábil (APLICACAO=entrada,
RESGATE=saída em conta de aplicação).

---

## Report #3 — CSV grande (Banco Inter) falha no parse com mensagem enganosa

**Status:** 🟢 Corrigido (em PR p/ `feat/fase1-cartao`). Causa corroborada por log de prod; fix =
teto de tokens + detecção de truncamento. **Raiz independente de #1/#2.**

### Report

> _"@Pedro testando a ferramenta da DM Construções o sistema não processou em CSV, apareceu esse
> erro... pedindo para verificar integridade do arquivo e se é protegido por senha."_ — 27/06/2026

- **Cliente:** DM Construções · Banco Inter (077) · conta corrente · Maio/2026. **Em prod.**
- **Arquivo:** CSV ~18,5 KB, **~220 lançamentos** (estimado do tamanho; extrato Banco Inter, muitos
  PIX com nomes longos).
- **Erro exibido:** _"...Verifique se o arquivo está íntegro e sem proteção por senha."_

### Diagnóstico (causa raiz — alta confiança)

1. **O CSV é corretamente detectado como CSV** ([`magic_bytes.py`](../../apps/api/app/utils/magic_bytes.py)
   — tem `;`+`\n`, decodifica latin-1) → **não** cai no caminho XLSX. O `_decode_text` do client faz
   fallback latin-1 com `errors="replace"`, então **não é erro de encoding** (apesar do mojibake do
   arquivo).
2. **O erro real é `AnthropicParseError`** ([`exceptions.py:208`](../../apps/api/app/core/exceptions.py#L208))
   — "JSON inválido ou `transactions` vazio" da extração via IA. A mensagem é genérica e fala em
   "proteção por senha" (conceito de PDF) → **enganosa para CSV** (sub-bug de UX).
3. **Causa raiz: truncamento de output.** `extract_movements` usa `max_tokens=8192`
   ([`client.py:59`](../../apps/api/app/integrations/anthropic/client.py#L59)). ~220 transações × ~40–50
   tokens de JSON cada (descrições PIX longas) ≈ **10–13k tokens de saída ≫ 8192** → o `tool_use` é
   cortado no meio do array → input incompleto/ inválido → `AnthropicParseError`.
4. **Log de prod (26/06) corrobora.** Na janela do teste (12:22–12:55 UTC):
   - 4× `anthropic_tool_validation_failed` com **`error_count=1`** (a DM não aparece nos `extract_ok`,
     logo é uma dessas) — bate com **1 transação final truncada** (objeto incompleto = 1 erro Pydantic).
   - Os maiores `anthropic_extract_ok` da janela tiveram **119** e **88** transações (e levaram **81–95 s**!).
     O **maior sucesso = 119**; a DM tem mais → estourou o teto. _(Confirma o limiar: < ~120 passa, ~220 trunca.)_
   - `stop_reason` **não** é logado no `extract_movements` (só na qualificação) — por isso o teste do
     CSV curto fecha 100%.
5. **Corroboração de design:** a Camada 1 da **qualificação JÁ sofreu exatamente isso** — comentário em
   [`semantic.py:42`](../../apps/api/app/modules/reconciliations/qualification/semantic.py#L42): _"o
   valor antigo (4096) TRUNCAVA o tool_use em extratos grandes, devolvendo `results` vazio"_ — e foi
   mitigada com batching de 50 + `max_tokens=8192` + log `qualification_semantic_truncated`. O
   `extract_movements` ficou com 8192 **sem batching e sem checar `stop_reason==max_tokens`** → fica
   vulnerável a extratos grandes (CC com centenas de PIX é o pior caso).

### Como confirmar

- **Log de prod — FEITO (26/06):** corrobora (ver Diagnóstico §4). Query usada:
  `gcloud logging read 'resource.type="cloud_run_revision" AND ("anthropic_tool_validation_failed" OR "anthropic_extract_ok")' --project=liberdade-assessoria --freshness=2d`.
- **Teste do CSV curto — PENDENTE (clincher):** subir o MESMO CSV cortado p/ ~30 linhas. Se
  processar → truncamento confirmado 100%. Se falhar mesmo curto → NÃO é tamanho (seria formato/
  conteúdo de alguma linha) e reinvestigamos.

### Opções de fix

- **(a) Subir `max_tokens`** do extract (ex.: 32k/64k — Sonnet/Opus suportam). Simples; extrato
  gigante ainda pode estourar.
- **(b) Chunkar a extração** em lotes de N linhas (como a qualificação faz 50/lote) e mesclar.
  Robusto p/ qualquer tamanho; mais complexo (continuidade de saldo, dedup, período).
- **(c) Parser determinístico p/ CSV** (Data;Descrição;Valor;Saldo é estruturado — dispensa IA).
  Barato e robusto, mas exige tratar formato por banco (Inter, etc.).
- **(d) Detectar truncamento** (`stop_reason==max_tokens`) + log + **corrigir a mensagem enganosa**
  ("extrato muito grande / divida o período"; tirar "proteção por senha" p/ CSV). _Quick win de UX
  independente do resto._

### Decisão

**✅ Implementado (a)+(d)** — commit `b90f1fe`, branch `fix/extracao-robustez` → PR p/ `feat/fase1-cartao`:

- `_MAX_OUTPUT_TOKENS` 8192 → **32768** (cobre ~480 linhas; é só teto, não custa mais p/ extrato pequeno).
- `extract_movements` detecta `stop_reason=max_tokens` → loga `anthropic_extract_truncated` + devolve
  erro acionável _"envie um período menor / divida em quinzenas"_ (some a msg enganosa de "senha").
- Teste unitário do truncamento. Gate verde (544 pytest, sem regressão).
- **(b) parser determinístico** fica como melhoria futura (não necessária agora — o teto resolve os
  casos reais; o erro acionável cobre o extremo).

---

## Report #4 — XLSX (DM) extrai só 1 de ~20 transações ("só as 3 primeiras linhas")

**Status:** 🟢 Corrigido (em PR p/ `feat/fase1-cartao`). Causa **confirmada por repro** (openpyxl
`read_only` + `<dimension>` errada); fix = remover `read_only`. **Raiz: robustez da extração (junto
com #3, mecanismo diferente).**

### Report

> _"@Pedro teste também em xlsx não funcionou 100%, só puxou as 3 primeiras linhas do extrato."_
> — 27/06/2026

- **Cliente:** DM Construções · **XLSX** (mesmo extrato do #3, em planilha) · Maio/2026. **Em prod.**
- **Preview:** BANCO **"Desconhecido"**, **1 movimentação** (21/05 PIX ENVIADO −R$ 30.000,00), saldos
  vindos das linhas 11/13 da planilha.
- **Real:** o XLSX tem ~20+ lançamentos (linhas 11–43). Só **1** foi extraído.

### Diagnóstico (causa provável — corroborada por log)

1. **Pipeline XLSX:** `_xlsx_to_text` (openpyxl) renderiza a planilha em TSV → manda pro IA como
   bloco de texto ([`parse_service.py:217-219`](../../apps/api/app/modules/reconciliations/parse_service.py#L217)).
2. **NÃO é truncamento de output (#3).** Só 1 transação saiu → output minúsculo. O problema é o
   **input incompleto**: o IA só "viu" ~3 linhas (daí o BANCO "Desconhecido" e os saldos das 1ªs linhas).
3. **Log de prod corrobora:** 3× `anthropic_extract_ok` com `transaction_count=1` e **`bytes_in=432`**.
   Para XLSX, `bytes_in` = **tamanho do TEXTO RENDERIZADO** (`_prepare_content` faz `text.encode()`).
   **432 bytes ≈ ~3 linhas**; a planilha cheia (~43 linhas × 6 colunas com nomes/CNPJs) renderizaria
   vários KB → **`_xlsx_to_text` leu só ~3 linhas.** (3× = colega re-testou o mesmo arquivo. _Confirmar
   timing: foram ~9:30–9:48 BRT?_)
4. **Mecanismo provável:** `openpyxl.load_workbook(..., read_only=True)`
   ([`parse_service.py:243`](../../apps/api/app/modules/reconciliations/parse_service.py#L243)) confia
   na tag `<dimension>` do arquivo no modo streaming; **XLSX exportado por banco frequentemente tem
   `<dimension>` ausente/errada** → o leitor read_only para nas poucas linhas declaradas. (A confirmar:
   inspecionar a `<dimension>` ou testar sem `read_only` / após re-salvar no Excel.)

### Como confirmar

- **Timing:** os 432-byte/txn=1 events batem com a hora do teste do XLSX? (cheap)
- **Decisivo (não precisa do arquivo sensível):** re-salvar o XLSX no Excel (Save As .xlsx — reescreve
  a `<dimension>`) e re-subir. Se extrair **tudo** → confirma `read_only` + dimension. Se continuar 1 →
  reinvestigar (`max_row`/abas/merge).

### Opções de fix

- **(a) `_xlsx_to_text` sem `read_only`** (ou `ws.reset_dimensions()` / `calculate_dimension(force=True)`)
  → lê todas as linhas. Simples e direto; custo: mais memória em XLSX gigante (aceitável — extrato
  mensal é pequeno). **← fix mínimo do #4.**
- **(b) Parser determinístico p/ CSV/XLSX estruturado** (Data/Lançamento/Valor/Saldo é tabular —
  dispensa IA). Mesma opção (c) do #3 → **resolve #3 e #4 de uma vez** e elimina a fragilidade da IA
  em extrato real.

### Decisão

**✅ Implementado (a)** — commit `8107584`, branch `fix/extracao-robustez` → PR p/ `feat/fase1-cartao`:

- `_xlsx_to_text` sem `read_only=True` → lê todas as células de fato (não depende da `<dimension>`).
- **Causa confirmada por repro** (script `repro_xlsx_dimension.py`): com `<dimension>` adulterada,
  `read_only=True` → 1 linha; `read_only=False` → 40. Teste unitário de regressão fixa o comportamento.
- Gate verde (544 pytest, incl. `test_parse_endpoint` — sem regressão no caminho XLSX normal).
- **(b) parser determinístico** fica como melhoria futura (a extração via IA é frágil em extrato real —
  vale reconsiderar um parser p/ CSV/XLSX estruturado depois; IA fica essencial p/ PDF/fatura).
