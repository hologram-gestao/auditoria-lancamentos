# Fixtures REAIS da API Omie (BACK 02.7)

> ⚠️ **S-3 (ASSUMIDA — NÃO TESTADA / RISCO):** assume-se que os nomes de campo
> da Omie hoje em uso estão corretos. Se falso, a integração quebra em produção
> de novo — **já aconteceu**: os nomes da v1 do `ListarExtrato` (`nCodLanc`,
> `dDtLanc`, `nValorLanc`, `cDescrLanc`) estavam "TODOS errados"
> (`omie/schemas.py`), corrigidos por INCIDENTE, não por teste. Idem
> `ListarContasCorrentes` e o filtro (`filtrar_por_conta_corrente` devolvia erro
> 5001). **Um teste com os mesmos nomes inventados confirma a invenção** — por
> isso um mock escrito à mão NÃO conta.

## O que é isto

O objetivo do BACK 02.7 é **capturar UMA chamada real** de cada endpoint e
gravá-la como fixture, para que os testes rodem contra a **resposta real** (não
contra a documentação). Se a resposta real divergir do schema atual
(`app/integrations/omie/schemas.py`), o teste `tests/unit/test_omie_fixtures.py`
**FALHA** e a divergência fica registrada — exatamente o que não aconteceu antes.

## ⚠️ Por que estas fixtures ainda NÃO estão aqui

A captura **exige uma credencial Omie real de um cliente autorizado** (ex.:
Quial) + acesso à rede da Omie. **A Omie não tem sandbox** (CLAUDE.md §10). O
agente de backend que preparou esta task **não tem acesso a credencial real nem
à rede da Omie** neste ambiente — portanto **não fabricou fixtures** (fabricar
seria repetir o erro que a task existe para prevenir).

O que foi entregue: o **harness pronto** (script de captura + teste que roda
contra as fixtures assim que existirem). **Falta**: um operador com credencial
autorizada rodar o script de captura.

## Como capturar (operador com credencial autorizada)

```bash
cd apps/api
export OMIE_CAPTURE_APP_KEY=...        # app_key do cliente autorizado (ex.: Quial)
export OMIE_CAPTURE_APP_SECRET=...     # app_secret
export OMIE_CAPTURE_CONTA_ID=...       # nCodCC de uma conta corrente com MUITOS movimentos
export OMIE_CAPTURE_PERIODO_INICIAL=01/04/2026
export OMIE_CAPTURE_PERIODO_FINAL=30/04/2026
uv run python -m scripts.capture_omie_fixtures
```

Gera, neste diretório, para cada endpoint:

- `<endpoint>.request.json` — request enviado (SEM app_key/app_secret).
- `<endpoint>.response.json` — resposta crua da Omie.

Endpoints capturados: `listar_extrato`, `listar_contas_correntes`,
`listar_contas_pagar`, `listar_contas_receber`.

## Antes de commitar — sanitização

- **Segredos:** `app_key`/`app_secret` **nunca** entram na fixture (o script já
  os omite do request; a resposta da Omie não os contém).
- **PII do cliente final:** a resposta traz dados reais (nomes de fornecedor,
  CNPJ, valores). **Anonimize os VALORES** (troque nomes/CNPJs por equivalentes
  fictícios) **mantendo os NOMES DE CAMPO e a ESTRUTURA verbatim** — são os
  nomes de campo que o teste verifica. Não altere chaves, tipos nem o envelope.

## Confirmação de paginação do `ListarExtrato`

Capture o `listar_extrato` contra uma conta com **muitos movimentos**. O código
assume que `ListarExtrato` **NÃO pagina** (`omie/client.py`: sem `pagina`/
`total_de_paginas`). Se a resposta real trouxer `total_de_paginas`/`pagina`, o
contrato mudou — registre e ajuste o `listar_extrato` para paginar (page size
100 CC / 50 pagar-receber, como os demais).
