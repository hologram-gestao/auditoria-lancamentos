# Teste-ouro da planilha do plano de contas da Omie (86e3qhhrr)

Entrada: o plano do Domínio anonimizado (`../accounting_chart_dominio_xls/plano_dominio.xls`,
370 contas, na ordem do plano) com os vínculos das relações anonimizadas
(`../accounting_chart_dominio_parties/`): as 26 contas analíticas que os dois têm em comum
saem com o CNPJ/CPF. A natureza vem de `nature.derive_nature`.

- `expected_parts.json`: o SHA-256 de cada parte do pacote XLSX gerado, DESCOMPRIMIDA (o
  hash do zip dependeria da versão da zlib; o das partes não). Só `sheet1.xml` e
  `sharedStrings.xml` diferem do modelo.
- `expected_cells.csv`: as células B a J das linhas preenchidas, como o `openpyxl` as lê,
  para o diff de uma falha ser legível.

Os documentos são os fictícios da fixture das relações (válidos, nenhum real).

Regenerar depois de uma mudança DELIBERADA no gerador ou no modelo:

    UPDATE_OMIE_GOLDEN=1 uv run --extra dev pytest tests/unit/test_omie_chart_sheet.py
