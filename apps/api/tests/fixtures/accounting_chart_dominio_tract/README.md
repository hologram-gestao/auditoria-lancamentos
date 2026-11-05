# Plano de contas da Tract exportado do Domínio em .xls, anonimizado (86e3ydk0c)

**Origem REAL, anonimizada.** O plano de contas que a Tract (cliente da Hologram) exportou
do Domínio em 04/11/2026, depois da reunião de 30/10/2026 em que a importação de um export
do Domínio deu "linha fora do padrão". O original tem razão social e CNPJ no banner, nome
de conta, rodapé com assinatura e as propriedades do Office com o nome do escritório: ele
ficou FORA do repositório. Esta cópia foi gerada por
`apps/api/scripts/anonymize_dominio_chart_sample_xls.py` (o mesmo script do `.xls` do
Domínio, estendido nesta task), que recebe o caminho do original por argumento e confere, no
fim, que nenhum texto trocado sobrou (146 textos distintos procurados, 0 encontrados). Uma
conferência independente, com os 143 textos de célula do original lidos pelo leitor da
plataforma, o nome do escritório em qualquer caixa e os dígitos do CNPJ do nome do arquivo,
também deu 0.

## Diagnóstico (o que o arquivo real fez na importação)

Lido pelo `parse_chart_sheet` local antes de qualquer outra coisa: **entra inteiro pelo
caminho do Domínio** (cabeçalho exato na linha 7, `convert_dominio`), 150 contas, 63
sintéticas, nenhuma linha com problema, igual com e sem a prévia das linhas com problema.
Não precisou de correção no leitor. O "linha fora do padrão" da reunião de 30/10 NÃO se
reproduz com este arquivo, e o arquivo daquela tentativa não chegou: sem ele, aquela falha
continua sem diagnóstico.

## O que este arquivo tem de diferente dos outros exports do Domínio

A mesma versão de colunas da amostra do Gabriel (`accounting_chart_dominio/` e
`accounting_chart_dominio_xls/`): cabeçalho desalinhado do dado (`Código` na coluna B, o
código na A; `Grau` na Y, o grau na X), marca `S` na D, classificação na H e o nome numa
coluna que anda com o grau. Diferenças:

- **regravado pelo Excel**, não o `.xls` que o Domínio grava: abre direto no xlrd (sem os
  BLANKs órfãos que `reader._first_sheet_at_bof` repara), tem o registro WRITEACCESS e os
  dois streams de propriedades do Office (59.392 bytes);
- o banner tem também `Emissão:` e `Hora:` (data e hora do relatório, em claro: não
  identificam ninguém) e uma linha vazia a mais, então o cabeçalho está na **linha 7**, e as
  contas nas linhas 8 a 157;
- o rodapé é uma linha só (161);
- é um **recorte** do plano: 150 contas contra as 1850 do plano "ajustado"
  (`accounting_chart_tract/`). Toda sintética tem filho no arquivo, e as 150 estão no
  ajustado com o MESMO código e o MESMO tipo. Quatro analíticas têm outra classificação
  (`1232`, `1330`, `1620`, `2078`): o Domínio dá a cada uma a classificação de OUTRA conta
  (346, 1243, 1418 e 324, o caso da 86e3uyzcf) e o ajustado as renumerou dentro do mesmo
  grupo. Só o par 1243/1330 veio no recorte, então o arquivo tem 149 classificações para
  150 contas, e a importação aceita (a chave da conta é o código reduzido). Quatro contas
  têm outro nome (fora caixa, acento e espaço). As 1700 contas que faltam são, na maior parte, as contas
  individuais de cliente e de fornecedor (1.1.2.01 e 2.1.3.01).

## O que foi mantido

O arquivo byte a byte, fora o texto (a troca é no lugar, com o mesmo número de caracteres):
contêiner OLE2, registros BIFF, posições de célula, código e grau como número, marca `S`,
classificação como texto, a data e a hora do banner.

## O que foi trocado

- o valor ao lado de `Empresa:` e de `C.N.P.J.:` → `EMPRESA EXEMPLO LTDA` e
  `00.000.000/0001-00`, cortados ou completados até o comprimento original; os rótulos
  `Emissão:` e `Hora:` (fora da lista de rótulos do script) também viram texto fictício;
- o nome de TODA conta → `Conta de exemplo <classificação>` ou `Grupo de exemplo
<classificação>`, no comprimento do original;
- o rodapé → o texto fixo fictício;
- o usuário do WRITEACCESS → `Hologram OS (fixture anonimizada)` cortado no comprimento;
- os streams `SummaryInformation` e `DocumentSummaryInformation` → um conjunto de
  propriedades VAZIO, do mesmo tamanho.

## Arquivos

| Arquivo              | O que é                                                                                                       |
| -------------------- | ------------------------------------------------------------------------------------------------------------- |
| `plano_dominio.xls`  | O export anonimizado.                                                                                         |
| `plano_esperado.csv` | UTF-8, `;`: as 150 contas que a importação TEM de produzir (`linha;codigo_reduzido;nome;tipo;classificacao`). |

O teste-ouro é `tests/unit/test_accounting_chart_dominio_tract.py`; a importação pela API,
`tests/integration/test_accounting_chart_content_import.py::TestExportCruDaTract`.
