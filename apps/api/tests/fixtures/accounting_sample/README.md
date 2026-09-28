# Amostra contábil anonimizada (Sprint 16, insumo da Sprint 13)

**Origem REAL, anonimizada.** Um mês (agosto/2026) de um cliente sem ERP atendido por um
escritório contábil parceiro: o extrato que o cliente manda, o de-para que o escritório
aplica e o CSV de lançamentos que ele importa no sistema contábil. Os arquivos originais
ficaram fora do repositório.

**O que foi trocado:** nomes de inquilinos, fornecedores, pessoas físicas, do cliente, do
escritório e da localidade do imóvel viraram rótulos fictícios (`Inquilino A`…`F`,
`Pessoa X`/`Y`, `Cliente Exemplo`, `Galpao Centro`…), e o número da conta bancária virou
`1234567-8`. **O que foi mantido:** datas, valores, códigos reduzidos das contas, números
de NF e a estrutura dos textos. **Os nomes das contas em `plano_contabil.csv` são
FICTÍCIOS**: o plano do cliente não veio na amostra, só os códigos usados.

## Arquivos (`cliente_exemplo_2026_08/`)

| Arquivo | Formato | O que é |
| --- | --- | --- |
| `extrato_cliente.csv` | UTF-8, `;`, cabeçalho, `dd/mm/aaaa`, vírgula decimal, valor com sinal | Os 32 movimentos do extrato, já limpos (o original é um XLSX com título, seções e linhas de saldo, que a leitura da Sprint 14 recusaria). Descrições com espaço no fim, como no original. |
| `plano_contabil.csv` | UTF-8, `;`, cabeçalho | As 20 contas usadas no mês: código reduzido, nome (fictício), tipo. |
| `decisoes_depara.csv` | UTF-8, `;`, cabeçalho | 23 decisões: categoria de origem (a descrição aparada, modo `classificacao_livre`) → conta contábil de contrapartida + histórico padrão. |
| `lancamentos_esperados.csv` | **Latin-1, `;`, CRLF, SEM cabeçalho** | O arquivo que o sistema contábil recebe: `data;débito;crédito;R$ valor;histórico`. Colunas 1 a 4 idênticas ao original, byte a byte. Travado como `-text` no `.gitattributes` desta pasta. |

**Conta do banco:** `649` é a conta contábil do banco; o arquivo não tem coluna de conta,
então ela é a conta padrão do cliente. Entrada → débito `649`, crédito na conta da decisão;
saída → débito na conta da decisão, crédito `649`.

## Números para conferência

| | Valor |
| --- | --- |
| Movimentos | 32 (15 entradas, 17 saídas) |
| Entradas | `29.377,33` |
| Saídas | `24.193,66` |
| Saldo anterior (31/07) | `100.950,63` |
| Saldo final (31/08) | `106.134,30` |
| Linhas `nao_mapear` / sem categoria | 0 / 0 |

Com o histórico fixo por decisão, as 32 linhas são reproduzíveis: nenhuma descrição
repetida no mês pede histórico diferente. Não há duas linhas com a mesma descrição e
contas diferentes.
