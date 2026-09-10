---
name: matcher
description: >
  Roteiro OBRIGATÓRIO ao tocar apps/api/app/modules/reconciliations/processing/
  (matcher, name_affinity, omie_fetch, anomalies, split_payment_probe, job) ou ao
  explicar um resultado de cruzamento. Gatilhos literais: "matching", "conciliação
  não bateu", "tolerância de data", "tolerância de valor", "lançamento errado",
  "anomalia falsa", "pares casados", "sem_omie demais", "data divergente", "pagamento
  dividido". O motor é determinístico e suas invariantes (CLAUDE.md §5) já produziram
  incidente real quando violadas: mudança sem teste que falhe antes e sem medir pares
  é regressão silenciosa — e "menos pares" pode ser correção, não regressão.
---

# /matcher — mexer no motor de conciliação sem regredir

O cruzamento é a função pura `match()` em
`apps/api/app/modules/reconciliations/processing/matcher.py:156` — sem I/O, sem ORM,
sem log. O incidente que moldou as regras: Bruna, 04/08/2026, cliente Romilson
Carpintaria — o laço guloso antigo deixava uma linha sem contraparte legítima
(pagamento dividido em duas parcelas no Omie) roubar o lançamento de outra linha, e
**um pareamento errado gerava duas anomalias falsas**. Esta skill transforma a §5 em
invariantes verificáveis e num protocolo de mudança. Números de linha conferidos em
10/09/2026 — se um grep não bater, o código andou: releia o arquivo.

## Mapa do módulo (quem decide o quê)

| Arquivo (`processing/`)      | Decide                                                                                                                                                                                                    |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `matcher.py`                 | `match()`; `AMOUNT_TOLERANCE` (`:32`), `DATE_DIVERGENCE_RANGE` (`:38`); DTOs `FileEntryForMatch` (`:42`), `OmieMovement` (`:60`), `TieStats` (`:100`), `MatchResult` (`:119`)                             |
| `name_affinity.py`           | `supplier_affinity` (`:79`) — desempate por fornecedor, nunca exclusão                                                                                                                                    |
| `omie_fetch.py`              | `fetch_realized` (`:68`, expande o período), `fetch_pending` (`:137`, 4 chamadas), `deduplicate_by_id` (`:227`), sinal dos títulos (`:195`, `:215`)                                                       |
| `job.py`                     | Orquestra: decifra descrição (`:306-314`), busca (`:327-338`), `match` (`:371`), classifica por `days_diff` (`:378-396`), loga `reconciliation_matched` (`:398-412`), sonda (`:420-435`), aplica (`:444`) |
| `anomalies.py`               | `create_structural_anomalies` (`:150`): `missing_in_omie`, `missing_in_file` (só `Atrasado`, `:212`), `wrong_date` (`:227`)                                                                               |
| `split_payment_probe.py`     | `probe_split_payments` (`:79`) — SÓ conta, não altera match                                                                                                                                               |
| `balances.py`, `checksum.py` | Saldos e checksum do parser — fora do cruzamento                                                                                                                                                          |

```bash
ls apps/api/app/modules/reconciliations/processing/
```

## As 8 invariantes (nenhuma muda sem decisão explícita do stakeholder)

### 1. Valor: `|a − b| ≤ 0.01`, hard-coded, com sinal

`AMOUNT_TOLERANCE = Decimal("0.01")` (`matcher.py:32`), aplicada em
`_amount_within_tolerance` (`:151`). O sinal faz parte do valor: débito (negativo)
nunca casa crédito (positivo) — `test_opposite_sign_does_not_match`
(`tests/unit/test_matcher.py:98`). O sinal do lado Omie é normalizado ANTES, no
`omie_fetch` (extrato via `signed_amount`; título a pagar `-abs`, a receber `+abs`).

```bash
grep -n "AMOUNT_TOLERANCE\|def _amount_within_tolerance" apps/api/app/modules/reconciliations/processing/matcher.py
```

### 2. Data: `DATE_DIVERGENCE_RANGE = 3`, fixa, e quem classifica é o caller

`DATE_DIVERGENCE_RANGE: int = 3` (`matcher.py:38`). O matcher casa até o range e
devolve `days_diff_by_file_id`; `job.py:385-388` classifica: `0` → `conciliado`,
`1–3` → `conciliado_data_divergente` (+ anomalia `wrong_date`), `> 3` → sem par
(`sem_omie`). Vale para conta corrente E cartão
(`test_card_session_same_engine_with_parcelas`,
`tests/integration/test_reconciliation_job.py:532`). O parâmetro `tolerance_days` de
`match()` (`:159`) existe SÓ para testar o algoritmo (`:194-197`); o request ignora
`date_tolerance_days` (`app/modules/reconciliations/schemas.py:227-229`) e a coluna
grava 0 (`app/modules/reconciliations/service.py:320`).

```bash
grep -n "DATE_DIVERGENCE_RANGE: int\|tolerance_days: int = DATE_DIVERGENCE_RANGE" apps/api/app/modules/reconciliations/processing/matcher.py
grep -n "days_diff_by_file_id\[file_id\] == 0\|CONCILIADO_DATA_DIVERGENTE" apps/api/app/modules/reconciliations/processing/job.py
```

### 3. Período Omie expandido em 3 dias — em CINCO pontos, não três

A spec desta skill (14/08) falava em três lugares; o código tem cinco pontos em
quatro consumidores, todos importando a MESMA constante:

- processamento: `fetch_realized` (`omie_fetch.py:102-103`) e o cache da qualificação
  (`job.py:348-349`);
- tela de revisão (`/available-omie-entries`): `_expanded_session_period`
  (`app/modules/reconciliations/review/service.py:1209-1225`);
- export: `app/modules/reconciliations/export/service.py:386-387`;
- detalhe de lançamento (`omie_data`): `expand_period`
  (`app/modules/omie_data/service.py:84-86` → `review/repository.py:467`).

Mudar num só cria divergência silenciosa entre o que o matcher viu e o que a tela ou
o relatório mostram. O grep abaixo é a lista completa — linha nova aqui exige os
outros quatro revisados:

```bash
grep -rn "timedelta(days=DATE_DIVERGENCE_RANGE)\|expand_period(\|fetch_realized(" apps/api/app --include=*.py | grep -v "def "   # esperado: 8 linhas em 4 arquivos (job ×3, review ×2, omie_data ×1, export ×2)
```

### 4. Um lançamento Omie casa com UMA linha (1-para-1)

`used_omie_indices` (`matcher.py:204`, filtro `:223`, consumo `:278`) e
`matched_file_ids` (`:205`). Antes do matcher, `deduplicate_by_id`
(`omie_fetch.py:227`) remove o mesmo `omie_id` vindo do extrato E do título. No
banco, o índice parcial `ix_recon_file_entry_session_omie_unique`
(`app/db/models/reconciliation_file_entry.py:70`) impede dois vínculos ao mesmo
lançamento na sessão. Somar parcelas (N-para-1) é mudança ESTRUTURAL — ver "O que
NÃO é do matcher".

```bash
grep -n "used_omie_indices\|matched_file_ids" apps/api/app/modules/reconciliations/processing/matcher.py
grep -n "ix_recon_file_entry_session_omie_unique" apps/api/app/db/models/reconciliation_file_entry.py
```

### 5. Passadas por proximidade de data — e a ordem do arquivo não importa

`for pass_days in range(tolerance_days + 1)` (`matcher.py:216`): fecha TODOS os pares
de data exata, depois os de 1 dia, e assim por diante. Dentro da passada as linhas
decidem em `(transaction_date, id)` (`ordered_entries`, `:214`) — não na ordem de
leitura. A saída sai ordenada por linha do arquivo, não por passada (`:283-286`).
Travas: classe `TestMatcherPassadasPorProximidadeDeData` (`test_matcher.py:236`),
com o cenário da Bruna (`:247`) e a independência da ordem (`:288`).

```bash
grep -n "for pass_days in range\|ordered_entries = sorted" apps/api/app/modules/reconciliations/processing/matcher.py
```

### 6. Desempate: menor `|amount_diff|` → maior afinidade → `date asc` — nunca exclusão

`_sort_key` (`matcher.py:242-251`): `(abs(amount_diff), -affinity, date)`. A
afinidade (`name_affinity.py:79`) conta tokens significativos do fornecedor Omie
presentes na descrição do extrato — sem limiar, sem fuzzy, stopwords (`:36`) e
mínimo de 3 caracteres (`:60`). Entra DEPOIS do valor porque valor é fato e nome é
indício, e só ORDENA: `test_nome_que_nao_bate_nunca_impede_o_match`
(`test_matcher.py:434`) e `test_valor_manda_mais_que_nome` (`:456`). Título a
pagar/receber não traz nome (`OmieMovement.supplier=None`, `matcher.py:82`) → afinidade
0 → cai no critério de data. `TieStats` (`:262-276`) conta empates e quantos o nome
resolveu — é a ÚNICA medida disponível, porque o conjunto de candidatos não persiste.

```bash
grep -n "def _sort_key\|-affinity\|ties += 1\|broken_by_supplier += 1" apps/api/app/modules/reconciliations/processing/matcher.py
grep -n "Só desempate, nunca exclusão" apps/api/app/modules/reconciliations/processing/name_affinity.py
```

### 7. IA nunca decide match

`matcher.py` e `name_affinity.py` não importam nada da Anthropic. A IA entra em dois
lugares e nenhum é o cruzamento: extração do arquivo (`parse_service.py`) e a
qualificação (`qualification/service.py:88`, `qualify_session`), que recebe os pares
JÁ fechados em `match_pairs` (`:93`), monta DTOs de leitura (`QualificationPair`,
`:479-487`) e grava só anomalias e o flag `qualification_used_glossary` da sessão
(`:170-174`) — `situation` e `omie_lancamento_id` foram gravados antes, em
`apply_matches` (`job.py:444`).

```bash
grep -rn "anthropic" apps/api/app/modules/reconciliations/processing/matcher.py apps/api/app/modules/reconciliations/processing/name_affinity.py   # esperado: nada
grep -n "match_pairs: list\|update(" apps/api/app/modules/reconciliations/qualification/service.py   # o único update é o flag da sessão (:171); o resto são assinaturas
```

### 8. Fornecedor e descrição trafegam em memória — nunca log, nunca banco

A descrição vai decifrada para o matcher (`_safe_decrypt_description`,
`job.py:545-564`; falha → `""` e a linha cai no critério de data, sem derrubar o
processamento). `FileEntryForMatch.description` (`matcher.py:56`) e
`OmieMovement.supplier` (`:82`) morrem no processo. O único log do caminho leva só o
`file_entry_id` (`job.py:563`); `reconciliation_matched` loga contadores (`:398-412`).

```bash
grep -rn "log\.\(info\|warning\|debug\)(" apps/api/app/modules/reconciliations/processing/ --include=*.py | grep -i "description=\|supplier=" ; echo "esperado: nada (exit 1)"
```

## Protocolo de mudança

### Passo 1 — linha de base ANTES de mexer

```bash
cd apps/api && uv run --extra dev pytest tests/unit/test_matcher.py tests/unit/test_name_affinity.py tests/unit/test_split_payment_probe.py -q --no-cov
```

Registrado em 10/09/2026: **54 passed em 0,25 s** (36 + 9 + 9). Guardas de integração
que também travam o motor (exigem Docker — ver skill `gate`):
`test_exact_divergent_and_unmatched_in_one_session`
(`tests/integration/test_reconciliation_job.py:385`) e
`test_totalizadores_do_detalhe_batem_com_as_abas`
(`tests/integration/test_reconciliation_totals.py:240`).

### Passo 2 — teste que FALHA antes e passa depois

Toda mudança de comportamento nasce com um caso na classe da invariante que toca
(`test_matcher.py`: `TestMatcherAmountTolerance :81`, `TestMatcherDateTolerance
:107`, `TestMatcherTieBreaking :132`, `TestMatcherPassadasPorProximidadeDeData :236`,
`TestMatcherDesempatePorFornecedor :402`). Helpers `_file`/`_omie` (`:33-51`); para
exercitar fornecedor, construa `FileEntryForMatch(description=...)` e
`_omie(..., supplier=...)`. Prove que o teste falha contra o código antigo:

```bash
git stash push -- apps/api/app/modules/reconciliations/processing/   # guarda SÓ a mudança de código, o teste fica
cd apps/api && uv run --extra dev pytest tests/unit/test_matcher.py -q --no-cov -k "<nome_do_teste_novo>"   # esperado: FAILED
git stash pop
```

### Passo 3 — medir pares, e saber ler "menos pares"

Compare o resultado ANTES (B) e DEPOIS (A) sobre as MESMAS entradas. A ordem dos
critérios importa:

1. **Maximalidade (obrigatória).** Depois de A, não pode sobrar par possível: nenhuma
   linha sem par com um lançamento Omie sem par a `|days| ≤ 3` e `|amount| ≤ 0.01`.
   As passadas garantem isso por construção — se a mudança quebra, é regressão,
   independente de contagem.
2. **Pares de data exata nunca caem.** `count(days_diff == 0)` em A ≥ em B (nos
   20 mil cenários da passagem para passadas, o antigo nunca fechou mais exatos que o
   novo: 0 em 20.000).
3. **Par perdido é CORREÇÃO quando** o lançamento que saiu do par em A (a) casou com
   outra linha a `|days_diff|` menor, ou igual com maior afinidade — estava roubado;
   ou (b) sobrou porque a ex-parceira não tem contraparte 1-para-1 (pagamento
   dividido — a sonda conta em `fechariam_por_soma`). Qualquer outro caso cai no
   critério 1 e é regressão.
4. **Acompanham:** `sum(days_diff)` não sobe para o mesmo número de pares;
   `ties`/`broken_by_supplier` de `TieStats` explicam mudanças de escolha.

Sonda de maximalidade (cole no teste ou num script):

```python
matched_files = {f for f, _ in result.matches}
used = set(range(len(omie))) - set(result.unmatched_omie_indices)
for fe in (f for f in files if f.id not in matched_files):
    for i, om in enumerate(omie):
        if i in used:
            continue
        assert not (
            abs((fe.transaction_date - om.transaction_date).days) <= DATE_DIVERGENCE_RANGE
            and abs(fe.amount - om.amount) <= AMOUNT_TOLERANCE
        ), f"par possível deixado na mesa: {fe.id} x {om.omie_id}"
```

**Precedentes registrados — aceitos, com teste que quebra se alguém "consertar":**

- passadas por data: **0,4 % menos pares** em 20 mil cenários sintéticos, nenhum
  par errado — `test_priorizar_data_exata_pode_fechar_um_par_a_menos`
  (`test_matcher.py:353`);
- desempate por fornecedor: **0,07 %** de cenários com contagem diferente (14 a
  menos, 11 a mais), 2,2 % com escolha diferente —
  `test_desempate_correto_pode_custar_um_par_em_cascata` (`:498`).

**O que esses números NÃO são:** referência para uma mudança nova. O harness dos 20
mil cenários **não está versionado** (só os docstrings o citam — grep abaixo), e a
contrapartida **nunca foi medida em dado real**: a validação da Bruna em 14/08 foi de
operador (Romilson conciliou certo), não comparação antes/depois. Replay a partir do
banco é impossível — o conjunto de candidatos não persiste, e
`reconciliation_omie_entries.amount` (desde 02/09) cobre só os sem par. Mudança nova
= script próprio de medição (versionado ou anexado ao PR) + leitura dos logs abaixo.

```bash
grep -rn "20.000\|20 mil" apps/api/tests apps/api/scripts --include=*.py   # só docstrings: não há harness no repo
```

### Passo 4 — os sinais de produção (sem PII)

Três eventos estruturados, todos contadores, medem o motor daqui para a frente:

- `reconciliation_omie_fetched` (`job.py:361`): `realized`, `pending`, `deduped`;
- `reconciliation_matched` (`:398`): `total_file`, `matched`, `divergent`,
  `unmatched_omie`, `ties`, `tie_broken_by_supplier`;
- `split_payment_probe` (`:426`): `sem_omie`, `fechariam_por_soma`,
  `com_agrupamento_omie`, `nao_avaliadas`, `omie_sem_par`, `omie_com_lanc_relac`.

```bash
grep -n '"reconciliation_omie_fetched"\|"reconciliation_matched"\|"split_payment_probe"' apps/api/app/modules/reconciliations/processing/job.py
```

### Passo 5 — fechar

Rode a skill `gate` (a suíte de integração exige Docker) e feche com a skill
`entrega`. Invariante mudou? A §5 do CLAUDE.md muda na MESMA entrega (§13).

## O que NÃO é do matcher (para não consertar no lugar errado)

- **Pagamento dividido (N-para-1).** Decisão estrutural em aberto (86e2n4r6p, Fatia
  2); hoje só a sonda mede (`MAX_PARCELAS = 3`, `MAX_CANDIDATOS_POR_LINHA = 40`,
  `split_payment_probe.py:40-46`). Implementar soma no `match()` sem decisão é
  quebrar a invariante 4.
- **Matching ótimo global (Hungarian).** Fecharia mais pares E o caso da Bruna;
  rejeitado por auditabilidade. Reabrir só com medição em dado real mostrando perda.
- **Status Omie não filtra o match** (`OmieMovement.status`, `matcher.py:66-69`).
  `Atrasado` vira `missing_in_file`; `Previsto` fica sem anomalia
  (`anomalies.py:212`; `test_previsto_omie_entry_persisted_but_not_anomaly`,
  `test_reconciliation_job.py:776`).
- **Sinal, natureza `P/R` do cartão e nomenclatura de status** são da skill `omie`.
- **Contagem de "conciliados"** inclui `conciliado_data_divergente`
  (`CONCILIATED_SITUATIONS`, `app/modules/reconciliations/totals.py:59-64`) — o card
  soma os dois; o filtro "data exata" mostra menos, e a diferença é
  `conciliated_divergent_count`.
