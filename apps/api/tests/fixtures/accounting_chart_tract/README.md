# Plano de contas "ajustado" da Tract, anonimizado (86e3ydjyq, épico 86e3ydjy9)

**Origem REAL, anonimizada.** A planilha que a Tract (cliente da Hologram) mandou em
30/10/2026 para importar o plano contábil: um plano feito a partir do Domínio para a Omie,
que não segue o modelo da plataforma nem o export do Domínio. É o caso que levou ao
reconhecimento das colunas pelo CONTEÚDO. O original tem razão social e CNPJ da empresa no
banner e CNPJ/CPF e razão social de fornecedores: ele ficou FORA do repositório. Esta cópia
foi gerada por `apps/api/scripts/anonymize_tract_chart_sample.py`, que recebe o caminho do
original por argumento e confere, no fim, que nenhum texto nem documento dele sobrou no
XML da fixture (2312 textos e 264 documentos distintos procurados, 0 encontrados).

## O que foi mantido (a estrutura)

- aba "Plano de Contas", A1:G1855: banner nas linhas 1 a 3, linha 4 vazia, cabeçalho na
  linha 5 (`Código`, `Classificação`, `Tipo`, `Grau`, `Natureza`, `Nome da conta`, sem
  título na coluna G) e **1850 contas** nas linhas 6 a 1855;
- código reduzido, classificação, tipo (`S` em 180 contas, `A` em 1670), grau e natureza
  (`Devedora`, `Credora` e `—`) idênticos, na mesma ordem;
- a coluna G com o CNPJ/CPF do fornecedor em 263 contas (ou `0`);
- aba "Fornecedores Ajustados": banner nas linhas 1 a 3, cabeçalho na linha 5 (`Código`,
  `Conta`, `Razão Social`, `C.N.P.J./C.P.F.`) e as 289 pessoas abaixo, com o código e a
  conta idênticos (insumo do vínculo conta ↔ fornecedor, 86e3ydjzy).

## O que foi trocado

- o banner das duas abas → `EMPRESA EXEMPLO LTDA`, `C.N.P.J.: 99.999.999/9999-99`,
  `PLANO DE CONTAS (exemplo)`;
- o nome de TODA conta → `Conta de exemplo <classificação>` (analítica) ou
  `Grupo de exemplo <classificação>` (sintética), completado até o comprimento do original;
- todo CNPJ/CPF → um fictício do MESMO tipo, com a máscara quando o original tinha e a
  MESMA validade pelo dígito verificador; o mesmo original vira o mesmo fictício nas duas
  abas; `0` e valores sem forma de documento ficam;
- a razão social da segunda aba → `Fornecedor de exemplo <n>`;
- autor e "último a salvar" do arquivo.

**A coluna G do original é uma FÓRMULA** (busca o CNPJ na segunda aba pelo nome do
fornecedor). A plataforma lê o valor que o Excel guardou, e a fixture grava esse VALOR no
lugar da fórmula (que dependeria dos nomes originais).

## Arquivos

- `plano.xlsx`: a fixture;
- `plano_esperado.csv`: as 1850 contas que a importação tem de produzir, no modelo da
  plataforma (`linha;codigo_reduzido;nome;tipo;classificacao`), para o teste-ouro
  (`tests/unit/test_accounting_chart_recognizer.py`).
