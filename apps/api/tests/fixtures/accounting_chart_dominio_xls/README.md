# Plano de contas exportado do Domínio em .xls, anonimizado (86e3n70p6)

**Origem REAL, anonimizada.** O plano de contas de um cliente de um escritório contábil
parceiro, exportado do Domínio no `.xls` que o sistema grava (Excel 97-2003, BIFF8 num
contêiner OLE2; amostra recebida em 09/10/2026). O original tem razão social e CNPJ no
banner, nome de cliente em conta analítica e um rodapé com assinaturas: ele ficou FORA
do repositório (`amostras-modelos/`, ignorada pelo git). Esta cópia foi gerada por
`apps/api/scripts/anonymize_dominio_chart_sample_xls.py`, que recebe o caminho da amostra
por argumento e confere, no fim, que nenhum texto trocado sobrou na fixture (343 textos
distintos procurados no SST relido e nos bytes do arquivo, em latin-1 e em UTF-16, 0
encontrados).

## O que foi mantido (o arquivo, byte a byte, fora o texto)

A troca é NO LUGAR: cada string recebe um texto fictício com o mesmo número de
caracteres, então nenhum byte muda de posição. Ficam idênticos ao original o contêiner
OLE2 (147.995 bytes, tamanho que não é múltiplo do setor de 512), todos os registros
BIFF, e:

- **o defeito do export do Domínio**: 5.919 registros BLANK (célula só de formatação,
  sem valor) entre o fim dos globais e o início da aba, com o BOUNDSHEET apontando para
  eles e não para o BOF da aba. O xlrd puro recusa este arquivo ("Expected BOF record");
  o Excel abre. É o que `reader._first_sheet_at_bof` repara, e é o que o teste-ouro prova;
- o banner nas linhas 1 a 3 com os rótulos do Domínio e o número da folha;
- o cabeçalho na linha 5 (`Código`, `T`, `Classificação`, `Nome`, `Grau`), desalinhado do
  dado como no `.xlsx` do PR #279;
- as **370 contas** nas linhas 6 a 375: código reduzido e grau como NÚMERO, marca `S` da
  sintética (141), classificação como TEXTO e a coluna do nome que muda com o grau;
- o rodapé nas linhas 377 e 379.

## O que foi trocado

- o valor ao lado de `Empresa:` e de `C.N.P.J.:` → `EMPRESA EXEMPLO LTDA` e
  `00.000.000/0001-00`, cortados ou completados até o comprimento original;
- o nome de TODA conta → `Conta de exemplo <classificação>` (analítica) ou
  `Grupo de exemplo <classificação>` (sintética), com o comprimento do nome original:
  nome curto vira o começo do fictício (`Grupo`), nome longo é completado;
- todo texto do rodapé → o texto fixo fictício do script do `.xlsx`;
- o nome da aba.

O arquivo não tem os streams de propriedades do Office (autor, empresa): o original
também não tinha.

## Arquivos

| Arquivo              | O que é                                                                                                                                                              |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `plano_dominio.xls`  | O export anonimizado, como o escritório o envia.                                                                                                                     |
| `plano_esperado.csv` | UTF-8, `;`: as 370 contas que a importação TEM de produzir (`linha` física, `codigo_reduzido`, `nome`, `tipo`, `classificacao`). O teste-ouro compara conta a conta. |

## Amostra nova

Outro `.xls` do Domínio (outra versão, outro cliente): salve em
`amostras-modelos/planos-contabeis/`, gere a fixture com o script numa pasta nova ao lado
desta e acrescente-a ao teste-ouro (`tests/unit/test_accounting_chart_dominio_xls.py`). Se
o script recusar o arquivo ou o teste não passar, o layout mudou: a leitura NÃO afrouxa
sem essa evidência.
