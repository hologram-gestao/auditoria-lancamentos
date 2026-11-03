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

## A planilha não precisa seguir o modelo (86e3ydjyq)

Desde 03/11/2026 a planilha (XLSX ou XLS) que não é o modelo nem o export do Domínio é
lida pelo **conteúdo** das colunas (layout `reconhecido`; leitor:
`client_accounting_chart/recognizer.py`). O caso real foi a Tract: um plano "ajustado"
com banner, cabeçalho na linha 5 e as colunas `Código`, `Classificação`, `Tipo`, `Grau`,
`Natureza`, `Nome da conta` e o CNPJ do fornecedor. O modelo continua sendo o caminho
garantido; o reconhecimento é o que evita reescrever um arquivo que já tem tudo.

- **O bloco de contas** é o maior trecho contíguo de linhas com duas células ou mais, uma
  delas inteira ou com classificação. Banner acima e rodapé abaixo ficam fora; linha em
  branco no meio não corta o bloco.
- **O que o reconhecimento exige:** uma coluna de **código** (inteiros em pelo menos 90%
  das linhas, sem repetição), uma de **nome** (texto em pelo menos 90% das linhas; colunas
  de texto vizinhas que nunca dividem uma linha são uma só, como o nome recuado do Domínio)
  e uma de **tipo** (só `S`/`A`, `sintetica`/`analitica`; vazio vale analítica quando a
  coluna nunca traz `A`, como no Domínio).
- **Opcionais:** **classificação** (`1.1.2.01.004`) e **grau** (inteiro igual à
  profundidade da classificação; quando existe, é conferido linha a linha).
- **Ignorado:** natureza (`Devedora`/`Credora`), CNPJ/CPF e qualquer outra coluna. Coluna a
  mais nunca recusa.
- **O nome da coluna só desempata** (`Nome` contra `Fantasia`, por exemplo). Duas colunas
  candidatas ao mesmo papel sem cabeçalho que desempate deixam o papel em aberto.
- Uma coluna tem uma forma quando pelo menos 95% das células preenchidas a seguem: a folga
  é para as linhas com problema, que continuam listadas com motivo.
- **Recusa:** sem bloco de contas (ou um bloco sem coluna de texto), `CABECALHO_DIVERGENTE`
  com a frase do que foi reconhecido e do que falta ("Reconhecemos as colunas de código e nome; não encontramos
  a coluna de tipo (S ou A)") e `details.recognizedRoles`, `missingRoles` e `ambiguousRoles`,
  em papéis da plataforma (`codigo`, `classificacao`, `tipo`, `grau`, `nome`). Nenhum texto
  do arquivo sai na resposta nem no log (o log leva só os papéis e quantas colunas foram
  ignoradas).
- **Etapa de mapeamento (86e3ydjz4):** quando há um bloco de contas mas o reconhecimento não
  decide código, nome ou tipo, a resposta não é recusa: é **422 `MAPEAMENTO_NECESSARIO`** com a
  mesma frase e `details.colunas`, uma entrada por coluna do bloco: `indice` (0 = primeira),
  `cabecalho` (o texto do cabeçalho logo acima do bloco, ou `null`), `formaDetectada` (`inteiro`,
  `classificacao`, `tipo_sa`, `natureza`, `documento`, `texto`, `vazia` ou `mista`),
  `preenchimento` (0 a 100) e `papelSugerido`. O cabeçalho é a **exceção declarada** (a tela
  precisa dele para a pessoa escolher); só conta como cabeçalho uma linha com duas células ou
  mais, todas texto, logo acima do bloco (um banner de uma célula, com a razão social, nunca
  sai). Valor de célula do bloco nunca sai. A tela pergunta o papel de cada coluna e manda,
  na prévia e na importação, o campo multipart **`mapeamento`**:
  `{"colunas": {"codigo": 0, "classificacao": 1, "tipo": 2, "grau": 3, "nome": 5},
"tipoPelaHierarquia": false}`. Regras: uma coluna por papel e um papel por coluna; `codigo` e
  `nome` obrigatórios; `tipo` obrigatório salvo com `tipoPelaHierarquia: true`, que exige a
  `classificacao`, não convive com coluna de tipo e marca como sintética a conta que tem outra
  abaixo (só por essa escolha; a prévia devolve `tipoDerivado: {sinteticas, analiticas}`).
  Mapeamento de forma inválida, fora das colunas da planilha ou num CSV é 400. Com ele, o
  reconhecimento só acha o bloco e a planilha é lida pela escolha (layout `mapeado`). O
  mapeamento não é guardado. Na Tract, a hierarquia daria 152 sintéticas contra as 180 do
  arquivo: 28 grupos sintéticos não têm conta abaixo, que é o motivo de o tipo nunca ser
  derivado sem a pessoa pedir.
- **Só planilha:** o CSV continua só no modelo (ler um CSV cru pediria mudar o leitor
  compartilhado, e nenhum arquivo real chegou assim).
- **Regra de processo:** todo arquivo de cliente novo vira fixture anonimizada ANTES de uma
  regra mudar. As fixtures: `tests/fixtures/accounting_chart_tract/` (script
  `scripts/anonymize_tract_chart_sample.py`) e as duas do Domínio, que o teste prova que
  chegam ao MESMO plano pelo conteúdo, sem o cabeçalho.

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

| `error.code`            | Quando                                                                                 | `error.details`                                                                                                                                                                      |
| ----------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `FORMATO_NAO_SUPORTADO` | não é CSV nem planilha pelo conteúdo (PDF, HTML/XML, OLE2 que não é planilha)          | —                                                                                                                                                                                    |
| `ARQUIVO_INVALIDO`      | não abre ou não itera (zip quebrado, fora de UTF-8, limites de tamanho/linhas/colunas) | —                                                                                                                                                                                    |
| `ARQUIVO_INVALIDO`      | a planilha não tem nenhuma conta                                                       | `{"reason": "sem_contas"}`                                                                                                                                                           |
| `CABECALHO_DIVERGENTE`  | nem o modelo, nem o Domínio, nem o reconhecimento por conteúdo leem a planilha         | `missingColumns`, `repeatedColumns` e `expectedColumns` (nomes do MODELO), `unexpectedColumnCount`, `foundColumnCount`, `recognizedRoles`, `missingRoles`, `ambiguousRoles` (papéis) |
| `LINHAS_INVALIDAS`      | uma ou mais linhas inválidas (todas listadas de uma vez, até 50)                       | `lines: [{line, reason}]`, `total`                                                                                                                                                   |

`reason` ∈ `codigo_vazio`, `codigo_longo`, `codigo_invalido`, `codigo_repetido`, `nome_vazio`,
`nome_longo`, `tipo_invalido`, `classificacao_longa`. `line` é a linha física da planilha
(cabeçalho = 1).

No plano exportado do Domínio, `reason` também pode ser `codigo_ausente`,
`classificacao_ausente`, `grau_ausente`, `grau_divergente`, `linha_irreconhecivel` ou
`conta_fora_do_bloco`, e `line` é a linha que a pessoa vê no Excel (o banner e o cabeçalho
contam). Classificação repetida não é recusa: o Domínio aceita duas contas com a mesma
classificação e códigos reduzidos diferentes, e a chave da conta é o código reduzido.

## Linhas com problema: de fora por confirmação, e o selo (86e3ydjze)

Desde 03/11/2026 uma linha fora do padrão não recusa mais o arquivo inteiro, nos quatro
caminhos (modelo, Domínio, conteúdo e mapeamento):

- a **prévia** devolve `linhasComProblema: [{linha, motivo}]` (os motivos são os da tabela
  acima, nenhum novo; nunca a célula) e as contagens já sem essas linhas;
- a **importação** só grava sem elas quando o campo multipart `linhasExcluidas` (JSON,
  `[10, 12]`) traz EXATAMENTE a lista que o servidor recalcula no mesmo arquivo. Sem o campo,
  ou com uma lista diferente (o arquivo mudou, ou a revisão era de outro arquivo), é o 422
  `LINHAS_INVALIDAS` de sempre e nada é gravado;
- continuam recusando o arquivo **inteiro**: `codigo_repetido` (ambiguidade de chave) e mais
  de **20%** das linhas com problema (`MAX_PROBLEM_LINE_RATIO`: a leitura provavelmente está
  errada). Com tantos problemas numa coluna, ela nem chega a ser reconhecida (mais de 5% fora
  da forma) e a resposta é o `MAPEAMENTO_NECESSARIO`; mapeada, o teto recusa;
- cada importação que entra fica registrada em `client_accounting_chart_imports` (quem, quando,
  layout, contagens e as linhas de fora). A lista do plano (`GET …/accounting-chart`) devolve a
  última em `ultimaImportacao: {importadaEm, layout, contas, linhasExcluidas}`;
- com linha de fora na última importação, a tela mostra ao lado do título o selo vermelho
  **"Plano não está 100%"**: o tooltip diz quando e quantas, e o clique abre a lista linha ×
  motivo. A importação seguinte sem linha de fora faz o selo sumir. A resposta da importação
  ganhou `contasExcluidas` e o evento `plano_contabil_importado`, `contas_excluidas`.

## Clientes e fornecedores: relação sem título e o CNPJ do próprio plano (86e3ydjzy)

- **Relação que não é o relatório intacto do Domínio** (a aba "Fornecedores Ajustados" da
  Tract): o cabeçalho por nome exige só `Conta` e a coluna do documento (`C.N.P.J./C.P.F.`);
  `Código`, `Nome` e `Razão Social` são reconhecidos e ignorados, e outra coluna não recusa.
  Sem cabeçalho, o conteúdo decide: o documento é a coluna com cara de CNPJ/CPF e a conta, a
  de inteiros que existem como código no plano do cliente.
- **Sem o título** ("RELAÇÃO DE CLIENTES/FORNECEDORES"), a resposta é 422
  `TIPO_DE_VINCULO_NECESSARIO` e a tela pergunta "Esta relação é de clientes ou de
  fornecedores?" (dois botões, sem padrão); o reenvio leva o campo multipart `tipoVinculo`
  (`cliente` ou `fornecedor`). Com título, o título manda: `tipoVinculo` diferente é 422
  `TIPO_DE_VINCULO_DIVERGENTE`, nunca ignorado.
- **Sem o rótulo `C.N.P.J.:`** no banner, a relação entra sem conferir a empresa e sem
  tocar a impressão digital; a resposta diz `empresaConferida: false` e a tela avisa. Rótulo
  presente com valor que não é CNPJ continua sendo a recusa `empresa_nao_identificada`. O CNPJ
  na mesma célula do rótulo (`C.N.P.J.: 05.482.859/0001-06`) também é lido.
- **A coluna de CNPJ/CPF do plano** (a Tract a traz nas contas de fornecedor): a prévia devolve
  `documentosEncontrados` (contas com documento, fora o `0`) e a tela oferece "Vincular as N
  contas ao CNPJ/CPF que vieram no arquivo", desligado, com o tipo do lote pré-marcado
  "Fornecedores". A importação com `vincularDocumentos: {"tipo": "fornecedor"}` grava os
  vínculos na mesma transação, pela mesma regra da relação: `0` ou vazio
  (`documento_ausente`), verificador errado (`documento_invalido`), conta sintética
  (`conta_sintetica`) e o mesmo documento em duas contas (`documento_repetido`) ficam sem
  vínculo e voltam em `vinculos.naoVinculadas`. A empresa não é conferida nesse caminho.
- Na Tract (o banner traz o CNPJ na mesma célula do rótulo, então a empresa é conferida), a relação traz uma conta (`1260`) que não está no plano: a importação recusa o
  arquivo inteiro nomeando o código (a regra de sempre), e depois de incluir a conta à mão
  ("Nova conta") a relação entra com 275 contas vinculadas.

## Validador único de conta (consumido pelas 16.2 e 16.3)

`AccountingChartService.require_postable_account(client_id, account_id)`: conta de outro
cliente ou inexistente → **404** (sem vazar existência); conta **sintética** ou **inativa** →
**422 `CONTA_CONTABIL_NAO_LANCAVEL`** com `details.reason` ∈ {`sintetica`, `inativa`}.
