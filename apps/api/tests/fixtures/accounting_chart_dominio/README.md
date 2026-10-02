# Plano de contas exportado do Domínio, anonimizado (86e3gkd7y)

**Origem REAL, anonimizada.** O plano de contas de um cliente de um escritório contábil
parceiro, exportado do Domínio em `.xlsx` (amostra recebida em 02/10/2026). O original
tem razão social e CNPJ no banner, nome de cliente em conta analítica e um rodapé com
assinaturas, CPF e CRC: ele ficou FORA do repositório (`amostras-modelos/`, ignorada pelo
git). Esta cópia foi gerada por `apps/api/scripts/anonymize_dominio_chart_sample.py`, que
recebe o caminho da amostra por argumento e confere, no fim, que nenhum texto dela sobrou
no XML da fixture (532 textos distintos procurados, 0 encontrados).

## O que foi mantido (a estrutura, exatamente)

- a primeira aba, 572 linhas x 25 colunas, as **2.407 células mescladas** e as **2.410
  células preenchidas nas mesmas posições**, com o mesmo tipo de valor;
- o banner nas linhas 1 a 4 com os rótulos do Domínio (`Empresa:`, `C.N.P.J.:`,
  `PLANO DE CONTAS`, `Folha:`) e o número da folha;
- o cabeçalho na linha 5 (`Código`, `T`, `Classificação`, `Nome`, `Grau`), desalinhado
  do dado como no original (`Código` em B, o código em A; `Grau` em Y, o grau em X);
- as **563 contas** nas linhas 6 a 568: código reduzido, marca `S` da sintética (143),
  classificação, a coluna do nome que muda com o grau (L a P) e o grau, todos
  idênticos; os **40 grupos sintéticos sem filho** continuam lá;
- a linha em branco depois das contas e o rodapé nas linhas 570 e 572.

## O que foi trocado

- o valor ao lado de `Empresa:` e de `C.N.P.J.:` → `EMPRESA EXEMPLO LTDA` e
  `00.000.000/0001-00`;
- o nome de TODA conta → `Conta de exemplo <classificação>` (analítica) ou
  `Grupo de exemplo <classificação>` (sintética), completado até o comprimento do nome
  original quando ele era maior (nome curto ficou mais longo; o comprimento não é
  estrutura, a coluna é);
- todo texto do rodapé → um texto fixo fictício (`CPF 000.000.000-00`, `CRC 0XX000000/O-0`);
- autor e "último a salvar" do arquivo, e o nome da aba.

A fixture é regravada pelo openpyxl, então o XML não é o do Domínio byte a byte (o
original usa strings compartilhadas): o leitor da plataforma lê as duas formas, e a
amostra original continua sendo o teste manual de ponta.

## Arquivos

| Arquivo              | O que é                                                                                                                                                              |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `plano_dominio.xlsx` | O export anonimizado, como o escritório o envia.                                                                                                                     |
| `plano_esperado.csv` | UTF-8, `;`: as 563 contas que a importação TEM de produzir (`linha` física, `codigo_reduzido`, `nome`, `tipo`, `classificacao`). O teste-ouro compara conta a conta. |

## Amostra nova

Quando chegar outra exportação do Domínio (outra versão, outro cliente): salve em
`amostras-modelos/planos-contabeis/`, gere a fixture com o script numa pasta nova ao lado
desta e acrescente-a ao teste-ouro (`tests/unit/test_accounting_chart_dominio.py`). Se o
script recusar o arquivo ou o teste não passar, o layout mudou: a leitura NÃO afrouxa
sem essa evidência.
