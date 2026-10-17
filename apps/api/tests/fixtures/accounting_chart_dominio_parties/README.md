# Relações de clientes e de fornecedores do Domínio em .xls, anonimizadas (86e3qhhrn)

**Origem REAL, anonimizada.** Os relatórios "RELAÇÃO DE CLIENTES" e "RELAÇÃO DE
FORNECEDORES" que o contador parceiro exportou do Domínio para a Hologram (amostras
recebidas em 14/10/2026), no `.xls` que o sistema grava (Excel 97-2003, BIFF8 num contêiner
OLE2). O original tem razão social e CNPJ da empresa no banner e, em cada linha, o nome, a
razão social e o CNPJ ou CPF de uma pessoa: ele ficou FORA do repositório
(`amostras-modelos/dominio/`, ignorada pelo git). Estas cópias foram geradas por
`apps/api/scripts/anonymize_dominio_parties_sample_xls.py`, que recebe os caminhos das
amostras por argumento e confere, no fim, que nenhum texto trocado sobrou nos arquivos.

## O que foi mantido (o arquivo, byte a byte, fora o texto)

A troca é NO LUGAR (molde do plano em `.xls`, `accounting_chart_dominio_xls/`): cada string
do SST recebe um texto fictício com o mesmo número de caracteres, então nenhum byte muda de
posição. Ficam idênticos ao original o contêiner OLE2, todos os registros BIFF e:

- **o defeito do export do Domínio**: registros BLANK órfãos entre o fim dos globais e o
  início da aba, com o BOUNDSHEET apontando para eles. O xlrd puro recusa ("Expected BOF
  record"); `reader._first_sheet_at_bof` repara, e o teste-ouro prova;
- o banner nas linhas 1 a 3 (`Empresa:`, `C.N.P.J.:`, `Folha:`, `Emissão:`, `Hora:`), o
  TÍTULO do relatório na linha 5 (é de onde o tipo do vínculo sai) e o cabeçalho na linha 7
  (`Código`, `Conta`, `Nome`, `Razão Social`, `C.N.P.J./C.P.F./…`), com `Código` e `Conta`
  TROCADOS de lugar entre os dois relatórios, como no original;
- os números: `Código` (o cadastro no Domínio) e `Conta` (o código reduzido do plano) de
  cada pessoa, inclusive a conta `0` (sem conta) e a conta compartilhada por duas pessoas;
- a linha de total no fim.

## O que foi trocado

- o nome da empresa → `EMPRESA EXEMPLO LTDA`; o CNPJ dela → `00.000.000/0001-91` (válido);
- o nome e a razão social de toda pessoa → `Cliente de exemplo N` / `Fornecedor de exemplo N`,
  no comprimento do original;
- o documento de toda pessoa → um CNPJ ou CPF fictício, VÁLIDO, determinístico pela ordem
  da pessoa, com o mesmo tamanho do original (14 ou 11). Um documento que no original
  tivesse o dígito verificador errado viraria um fictício também inválido (o script
  preserva a validade); nas duas amostras não há nenhum;
- o nome da aba.

## Arquivos

| Arquivo                    | O que é                                                                                                                                                                                                              |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `relacao_clientes.xls`     | A RELAÇÃO DE CLIENTES anonimizada: 91 linhas de pessoa, 89 vinculáveis (77 CNPJ, 12 CPF; um CNPJ começa com zeros) e 2 que repetem uma pessoa já listada.                                                            |
| `relacao_fornecedores.xls` | A RELAÇÃO DE FORNECEDORES anonimizada: 93 pessoas, 88 vinculáveis, 3 com conta `0` e 2 que compartilham a conta `746`.                                                                                               |
| `vinculos_esperados.csv`   | UTF-8, `;`: linha física, relatório, conta, documento fictício, tipo do documento e situação (`vinculavel` ou o motivo do pulo). O teste-ouro (`tests/unit/test_accounting_chart_parties.py`) compara linha a linha. |

## Amostra nova

Outra relação do Domínio (outra versão, outro cliente): salve em `amostras-modelos/dominio/`,
gere a fixture com o script numa pasta nova ao lado desta e acrescente-a ao teste-ouro. Se o
script recusar o arquivo ou o teste não passar, o layout mudou: a leitura NÃO afrouxa sem essa
evidência.
