# Fixtures da origem por arquivo (Sprint 14)

**SINTÉTICAS.** Nenhuma linha veio de cliente real: nomes, valores e documentos foram
inventados no layout que a operação descreve em `i2.50` (planilha mensal com data,
histórico, valor com sinal, categoria e documento; CSV `;`, UTF-8, `dd/mm/aaaa`,
vírgula decimal). A rodada com arquivo REAL anonimizado é a validação humana em dev,
fora do sandbox do QA.

| Arquivo | Competência | Linhas | Total com sinal | O que prova |
| --- | --- | --- | --- | --- |
| `extrato_2026_07.csv` | 2026-07 | 8 | `1772,18` | mês 1: 6 categorias; duas linhas idênticas viram dois movimentos |
| `extrato_2026_08.csv` | 2026-08 | 6 | `-2710,42` | mês 2 sem reconfigurar: `Pró-labore` é nova e `Despesas bancárias` é outra GRAFIA de `Despesas Bancárias` (não é fundida) |

Usadas por `tests/integration/test_s14_qa_file_origin_cycle.py`.
