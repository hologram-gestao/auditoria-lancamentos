# Modelo da planilha de importação de plano de contas da Omie (86e3qhhrr)

`Omie_Plano_Contas_v1_1_3.xlsx` é o modelo `Omie_Plano_Contas` v1.1.3 do Painel do
Contador da Omie, **vazio**. O gerador (`app/modules/client_accounting_chart/omie_sheet.py`)
copia este arquivo e só preenche células da aba `Omie_Plano_Contas` e das strings
compartilhadas; todo o resto do pacote (aba `Config`, validações de dados, proteção,
estilos, desenho) sai byte a byte igual.

## De onde ele veio

O modelo chegou em 19/10/2026 pelo contador parceiro, PREENCHIDO com o plano de um
cliente real (nomes de conta, CNPJs e CPFs). Essa amostra fica fora do repositório
(`amostras-modelos/omie/`, ignorada pelo git). Este arquivo foi tirado dela por
`apps/api/scripts/extract_omie_chart_template.py`, que:

- mantém as linhas 1 a 5 (instruções e cabeçalho) e o rodapé do modelo, e deixa as
  linhas 6 a 10005 vazias com o estilo da linha vazia do modelo;
- devolve às validações de dados a área inteira (6 a 10005): colar os dados na amostra
  tinha tirado a validação das linhas coladas;
- deixa só as 46 strings do modelo, tira o `lastModifiedBy` e o caminho da pasta local;
- confere no fim que nenhuma string da amostra e nenhum documento sobrou.

## Mudou o modelo da Omie?

Versão nova do modelo é arquivo novo (`…_v<versão>.xlsx`), extraído pelo mesmo script a
partir de uma amostra nova, e a constante `TEMPLATE_PATH` aponta para ele. O teste-ouro
(`tests/unit/test_omie_chart_sheet.py`) falha até o ouro ser regenerado de propósito.
A prova de que a Omie aceita é humana: importar a planilha gerada no Painel do Contador.
