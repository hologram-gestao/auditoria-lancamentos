# Plano de contas contábil do cliente — modelo de planilha (Sprint 16, BACK 16.1)

> Fonte: `app/modules/client_accounting_chart/sheet.py` (o leitor) e a descrição da rota
> `POST /api/v1/clients/{client_id}/accounting-chart/import` no OpenAPI. Decisão do
> planejador registrada no ADR-086-BE. Se o leitor mudar, esta página muda junto.

⚠️ É o plano do **sistema contábil de destino** (onde o escritório lança), não o plano de
contas da origem da Sprint 10 (`/chart-of-accounts`, categorias do Omie).

## Endpoints

| Método | Rota                                                          | Quem                                                                      | O quê                                                                                                                                                    |
| ------ | ------------------------------------------------------------- | ------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`  | `/api/v1/clients/{client_id}/accounting-chart`                | quem alcança o cliente                                                    | lista paginada (`page`, `pageSize` ≤ 100), busca por prefixo de código (`code`), filtros `type` (`analitica`/`sintetica`) e `status` (`ativa`/`inativa`) |
| `POST` | `/api/v1/clients/{client_id}/accounting-chart/import`         | `manage_client_accounting_chart` (plataforma, admin, gerente da carteira) | importa ou reimporta a planilha, tudo ou nada; cliente encerrado = 409                                                                                   |
| `POST` | `/api/v1/clients/{client_id}/accounting-chart/import/preview` | `manage_client_accounting_chart`                                          | mostra o que a importação faria, sem gravar; cliente encerrado = 409                                                                                     |
| `GET`  | `/api/v1/clients/{client_id}/accounting-chart/export`         | quem alcança o cliente                                                    | baixa o plano neste modelo (CSV), com 1 linha `export` na trilha                                                                                         |

## O modelo

- **Formato:** CSV em UTF-8 (com ou sem BOM) separado por `;`, ou planilha do Excel, XLSX
  ou XLS (só a primeira aba). O contêiner é decidido pelos magic bytes, nunca pela extensão.
  O tipo é detectado pelo **conteúdo** (magic bytes), nunca pela extensão.
- **Cabeçalho na linha 1.** Colunas casadas pelo nome, em qualquer ordem, **sem outras
  colunas**. O nome casa por **grafia normalizada** (86e3n70p9): maiúsculas, acentos, espaços
  e hífens no lugar do `_`, e `*` ou `:` no fim não importam (`Código Reduzido`,
  `codigo reduzido`, `CODIGO-REDUZIDO` e `Classificação` valem). **Sinônimo não vale**:
  `codigo` sozinho, `conta` ou `nome da conta` são coluna desconhecida e recusam o arquivo. A
  regra é de grafia, não de vocabulário, para a recusa continuar previsível.

| Coluna            | Obrigatória | Regra                                                                                                                                                                                                                       |
| ----------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `codigo_reduzido` | sim         | o código que vai no arquivo contábil; letras, dígitos, `.` e `-` (sem espaço nem `;`), até 20 caracteres, **único** na planilha                                                                                             |
| `nome`            | sim         | até 200 caracteres; gravado **cifrado** com a chave do cliente                                                                                                                                                              |
| `tipo`            | sim         | `analitica` (recebe lançamento) ou `sintetica` (só agrupa); vale também `analitico`/`sintetico` e a inicial `a`/`s`; maiúscula e acento indiferentes (`Analítica`, `S` valem). `sint` ou outra abreviação é `tipo_invalido` |
| `classificacao`   | não         | a classificação hierárquica (`1.1.1.02.001`), até 40 caracteres                                                                                                                                                             |

- Linha totalmente vazia é pulada. Pelo menos **uma** conta é obrigatória.

Exemplo (é o formato de `tests/fixtures/accounting_sample/cliente_exemplo_2026_08/plano_contabil.csv`):

```csv
codigo_reduzido;nome;tipo;classificacao
10;Ativo circulante;sintetica;1.1
649;Banco conta movimento;analitica;1.1.1.02.001
662;Alugueis a receber - Inquilino D;analitica;1.1.2.01.004
```

## Plano exportado do Domínio (.xls ou .xlsx)

Desde 02/10/2026 (86e3gkd7y) o arquivo que o Domínio exporta também é aceito, sem
reescrever: a importação reconhece o layout e o converte para o modelo acima, que passa
pelas MESMAS validações e pela mesma gravação. Leitor: `client_accounting_chart/dominio.py`.
Fixture anonimizada e teste-ouro: `tests/fixtures/accounting_chart_dominio/`. Desde
09/10/2026 (86e3n70p6) o `.xls` que o Domínio grava também entra, sem edição, pelo MESMO
conversor (fixture: `tests/fixtures/accounting_chart_dominio_xls/`). O export do Domínio
em `.xls` traz milhares de células vazias de formatação antes da aba, com o índice da aba
apontando para elas; o leitor compartilhado (`client_file_ingestion/reader.py`) pula só
esse tipo de registro, que não tem valor, e recusa o arquivo se houver qualquer outro ali.

- **Detecção:** só planilha (XLSX ou XLS); uma das **10 primeiras linhas** tem exatamente as células
  `Código`, `T`, `Classificação`, `Nome` e `Grau`, nessa ordem (espaços nas pontas,
  maiúsculas e acentos ignorados, nenhuma célula a mais). Sem isso, vale o modelo da
  plataforma, e o arquivo que não é nenhum dos dois recebe o `CABECALHO_DIVERGENTE` de
  sempre. O CSV nativo do Domínio **não** é aceito (não há amostra).
- **Leitura por padrão de célula**, não por coluna (o cabeçalho é desalinhado do dado e
  o nome muda de coluna com o grau): em cada linha, as células preenchidas são
  `código (inteiro)` · `S` opcional · `classificação` · `nome` · `grau (inteiro)`.
  `S` = sintética, sem marca = analítica; o tipo nunca é inferido (grupo sintético sem
  filho existe de verdade). O grau tem de ser a profundidade da classificação.
- **Fim das contas:** a primeira linha sem código inteiro e sem classificação. O que vem
  depois (o rodapé com assinaturas) não é lido, só conferido: linha com cara de conta ali
  recusa o arquivo **inteiro** (`conta_fora_do_bloco`), para nenhuma conta ficar de fora
  calada.

## Ordem da lista

A lista (`GET …/accounting-chart`) sai na **ordem da classificação** (86e3n70p9): a sintética
em cima e as analíticas dela abaixo. **A classificação ordena como TEXTO, como no Domínio**:
sob o mesmo pai, `00001…` e `001…` são famílias distintas e saem uma inteira depois da outra,
e `1.1.10` vem antes de `1.1.2`. Por isso a planilha feita à mão usa **largura fixa por
nível** (`1.1.01`, `1.1.02`… `1.1.10`); sem isso, `1.1.10` aparece antes de `1.1.2`, como
apareceria no próprio Domínio. Conta **sem** classificação ordena pelo código reduzido com os
segmentos numéricos preenchidos com zeros (`9` antes de `10`); a chave dela começa por zeros,
então num plano misto essas contas vêm antes das classificadas.

A chave é **derivada na gravação**, nunca inferida na leitura:
`client_accounting_accounts.sort_key` (collation `C`, comparação por byte), calculada por
`client_accounting_chart/sort_key.py::chart_sort_key` em toda inserção e atualização (modelo e
Domínio), e a listagem só faz `ORDER BY sort_key NULLS LAST, code, id`. A collation `C` é parte
da regra: a do sistema (`en_US.utf8` da glibc) ignora o ponto e embaralharia a classificação.
A migration `b2f7c9e41d06` preencheu as linhas existentes com a mesma regra em SQL. Quem gravar
conta por outro caminho (inclusão manual) chama `chart_sort_key` e persiste o resultado. A
ordem atravessa a paginação (é SQL) e o seletor de conta do de-para a herda. As duas amostras
do Domínio (`tests/fixtures/accounting_chart_dominio/` e `…_dominio_xls/`) saem na ordem do
arquivo, e um teste de integração exige isso.

## Reimportação: só adiciona e atualiza (86e3qhhrm)

Casa por `codigo_reduzido`, numa transação só, e **nunca inativa nem apaga** (decisão do
Pedro, 14/10/2026: o plano exportado pode vir recortado, e uma reimportação parcial
desligaria contas em uso):

- código novo → conta nova (ativa);
- código existente → atualiza nome, tipo e classificação, se mudaram (a igual nem é
  regravada). A **situação não muda**: a conta inativa que vem na planilha continua inativa
  (reativar é pela edição da conta);
- código que não veio na planilha → fica como estava e ganha o selo **"não veio na última
  importação"** (`absentSince`, a data da primeira importação em que sumiu); o selo sai
  quando a conta volta numa planilha. A conta com o selo continua lançável: o selo avisa
  (ela pode não existir mais no sistema contábil), não bloqueia.

Resposta: `{"data": {"contas": N, "contasNovas": N, "contasAlteradas": N, "contasAusentes": N}}`
— só contagens.

**Prévia** (`POST …/accounting-chart/import/preview`, mesma permissão e mesmas recusas, não
grava): as contagens (novas, alteradas, sem mudança, ausentes, ausentes em uso por decisão do
de-para de qualquer vigência ou pela conta do banco, as que voltaram e as inativas que vieram),
`temMudancas` e as listas `novas`, `alteradas` (com os campos que mudam) e `ausentes` (com
`inUse`), até 200 itens cada. A tela mostra a prévia e só importa depois da confirmação.

## Exportação

`GET …/accounting-chart/export` devolve o plano inteiro (ativas e inativas, na ordem do plano)
neste MESMO modelo: CSV UTF-8 com BOM (para o Excel), `;`, cabeçalho
`codigo_reduzido;nome;tipo;classificacao`. Exportar e reimportar o arquivo não muda nada. Nome
que o Excel leria como fórmula (começa com `=`, `+`, `-` ou `@`) sai com um apóstrofo na frente
(`'- Juros pagos`), e a importação retira esse apóstrofo; o apóstrofo que não guarda fórmula é do
nome e fica. Plano vazio sai só com o cabeçalho (serve de modelo). Grava 1 linha `export` na
trilha de acesso; nome que não decifra recusa com 409 `PLANO_CONTABIL_INDECIFRAVEL`.

## Recusas (todas 422, nenhuma grava nada, nenhuma devolve conteúdo de célula)

| `error.code`            | Quando                                                                                 | `error.details`                                                                                                        |
| ----------------------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `FORMATO_NAO_SUPORTADO` | não é CSV nem planilha pelo conteúdo (PDF, HTML/XML, OLE2 que não é planilha)          | —                                                                                                                      |
| `ARQUIVO_INVALIDO`      | não abre ou não itera (zip quebrado, fora de UTF-8, limites de tamanho/linhas/colunas) | —                                                                                                                      |
| `ARQUIVO_INVALIDO`      | a planilha não tem nenhuma conta                                                       | `{"reason": "sem_contas"}`                                                                                             |
| `CABECALHO_DIVERGENTE`  | falta coluna obrigatória, sobra coluna desconhecida ou coluna repetida                 | `missingColumns`, `repeatedColumns` e `expectedColumns` (nomes do MODELO), `unexpectedColumnCount`, `foundColumnCount` |
| `LINHAS_INVALIDAS`      | uma ou mais linhas inválidas (todas listadas de uma vez, até 50)                       | `lines: [{line, reason}]`, `total`                                                                                     |

`reason` ∈ `codigo_vazio`, `codigo_longo`, `codigo_invalido`, `codigo_repetido`, `nome_vazio`,
`nome_longo`, `tipo_invalido`, `classificacao_longa`. `line` é a linha física da planilha
(cabeçalho = 1).

No plano exportado do Domínio, `reason` também pode ser `codigo_ausente`,
`classificacao_ausente`, `grau_ausente`, `grau_divergente`, `linha_irreconhecivel` ou
`conta_fora_do_bloco`, e `line` é a linha que a pessoa vê no Excel (o banner e o cabeçalho
contam). Classificação repetida não é recusa: o Domínio aceita duas contas com a mesma
classificação e códigos reduzidos diferentes, e a chave da conta é o código reduzido.

## Validador único de conta (consumido pelas 16.2 e 16.3)

`AccountingChartService.require_postable_account(client_id, account_id)`: conta de outro
cliente ou inexistente → **404** (sem vazar existência); conta **sintética** ou **inativa** →
**422 `CONTA_CONTABIL_NAO_LANCAVEL`** com `details.reason` ∈ {`sintetica`, `inativa`}.
