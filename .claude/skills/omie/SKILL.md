---
name: omie
description: >
  Roteiro OBRIGATÓRIO ao tocar apps/api/app/integrations/omie/ ou qualquer chamada
  ao ERP. Gatilhos literais: "chamar o Omie", "ListarExtrato", "ListarContasPagar",
  "ListarContasReceber", "ListarCategorias", "ConsultarCliente", "IncluirLancCC",
  "IncluirContaPagar", "campo novo do Omie", "lançar no Omie", fixture do Omie,
  omie_posting. A Omie não tem sandbox, responde erro com HTTP 200 e já quebrou
  produção 3x por campo copiado da doc (CLAUDE.md §6.8): payload NUNCA vem de
  memória nem da Docs/documentation/6 — vem da resposta real capturada.
---

# /omie — integração Omie validada contra resposta real

A CLAUDE.md §6.8 chama a API do Omie de "especialmente perigosa" — e o repositório
carrega a prova: os nomes do `ListarExtrato` v1 (`nCodLanc`, `dDtLanc`, `cStatus`…)
estavam TODOS errados e foram corrigidos por incidente, não por teste
(`app/integrations/omie/schemas.py:159-165`); `filtrar_por_conta_corrente` e o filtro
`"PREVISTO"` deram `5001` em produção. Esta skill transforma o histórico em 9 passos,
cada um com o comando que o prova. Números de linha conferidos em 10/09/2026 — se um
grep não bater, o código andou: releia o arquivo, não confie na linha.

## Passo 1 — Fonte da verdade é a resposta REAL, não a doc

Todo nome de endpoint, envelope e campo que entra no código vem de uma fixture
capturada da API real em `apps/api/tests/fixtures/omie/` (15 arquivos: 5 leituras com
`request`+`response`, 4 da escrita, README). O gate `tests/unit/test_omie_fixtures.py`
roda os DTOs contra elas e FALHA na divergência — 11 testes, e hoje nenhum skipa.

A doc interna (`Docs/documentation/6. Integração com API do Omie-20260424133624.md`) e
a doc oficial (`https://app.omie.com.br/api/v1/<module>/<endpoint>/`) servem para achar
o NOME do serviço; nunca para definir campo. A auditoria que listou os erros históricos
está em `Docs/AUDITORIA_OMIE_INTEGRACAO.md`.

Antes de declarar ou usar um campo, procure-o na fixture do endpoint:

```bash
grep -c '"<campo>"' apps/api/tests/fixtures/omie/<endpoint>.response.json   # 0 = NÃO existe na resposta real
cd apps/api && uv run --extra dev pytest tests/unit/test_omie_fixtures.py -q --no-cov   # esperado: 11 passed
```

Envelopes reais (chave do array): `listaMovimentos` (ListarExtrato),
`ListarContasCorrentes`, `conta_pagar_cadastro`, `conta_receber_cadastro`,
`categoria_cadastro`. Os paginados trazem `pagina`/`total_de_paginas`/`registros`/
`total_de_registros`; o extrato NÃO traz nenhum desses.

**O que ainda NÃO tem fixture:** `ConsultarCliente` (`ClienteOmie`, `schemas.py:436` —
campos da doc oficial, consumo fail-soft) e o request de títulos COM filtros: a captura
de `ListarContasPagar/Receber` foi sem `filtrar_por_status`/`filtrar_conta_corrente`
(só `PAGO`/`RECEBIDO` na fixture) — os nomes dos filtros foram verificados por incidente
de produção, não por fixture. Campo sem fixture → passo 2, nunca "por analogia": a Omie
chama o mesmo dado de `cCodCateg` na escrita, `codigo_categoria` nos títulos e `codigo`
nas categorias.

## Passo 2 — Não existe sandbox: capturar é falar com conta real

Toda captura roda contra a conta real de um cliente autorizado (CLAUDE.md §10; a de
21/08/2026 usou a conta da própria Hologram — cartão Inter). Consequências: credencial
só em env var da sessão, nunca em arquivo nem commit; se vazar, rotaciona; leitura é
inofensiva, escrita cria movimento financeiro (passo 8).

```bash
cd apps/api
export OMIE_CAPTURE_APP_KEY=... OMIE_CAPTURE_APP_SECRET=...
export OMIE_CAPTURE_CONTA_ID=<nCodCC>              # conta com MUITOS movimentos
export OMIE_CAPTURE_PERIODO_INICIAL=01/07/2026 OMIE_CAPTURE_PERIODO_FINAL=31/07/2026
export OMIE_CAPTURE_CLIENTE_CODIGO=<codigo>          # opcional: captura ConsultarCliente
uv run python -m scripts.capture_omie_fixtures        # só LEITURA sem OMIE_CAPTURE_ALLOW_WRITE
```

(`scripts/capture_omie_fixtures.py:90`, `_build_captures` — a lista de endpoints e o
`param` de cada um moram lá, iguais aos de produção.) Antes de commitar: anonimize os
VALORES de PII mantendo NOMES DE CAMPO e estrutura verbatim (README, "Antes de
commitar"); não mexa em `nCodLancamento`/`nCodLanc`. O gate `TestFixturesCarryNoSecrets`
(`test_omie_fixtures.py:165`) barra credencial dentro de fixture:

```bash
grep -l '"app_key"\|"app_secret"' apps/api/tests/fixtures/omie/*.json   # esperado: nada
```

**Nunca fabrique fixture.** JSON escrito à mão com os nomes que você assume confirma a
invenção (defeito P11 da Sprint 1). Sem fixture, o teste skipa citando o script — esse
é o estado honesto.

## Passo 3 — Toda chamada passa pelo `OmieClient.call` (e o que ele já faz)

`OmieClient` (`app/integrations/omie/client.py:146`) é o único lugar que fala com
`OMIE_BASE_URL`. Chamada nova = método tipado novo no client (padrão `listar_extrato`,
`:628`) que faz `model_validate` num schema de `schemas.py`; nunca `httpx` solto num
service.

O `call()` (`:184`) já trata o que a Omie tem de peculiar — não reimplemente:

- **Erro vem com HTTP 200.** Todo body é checado por `faultstring` (`:395-406`); auth →
  `OmieAuthError` (keywords `:71`, faultcodes `:86`), o resto → `OmieFaultError`.
  **Sem retry** em fault: é erro lógico.
- **5xx com header `OmieAPI-Error`.** Prefixos `1880`, `6 -`, `8020` são transitórios
  (rate limit, `:111-115`) → retry com backoff, dormindo o "Aguarde N segundos" que a
  própria Omie manda (`:314-329`). Qualquer outro código — `5001` "Tag não faz parte da
  estrutura" = NOME DE CAMPO ERRADO, `3102` = tipo/obrigatoriedade — é permanente →
  `OmieFaultError`. 5xx sem header = infra → retry; esgotou → `OmieServerError`.
- **Budget:** 5 tentativas, backoff 1→16 s (`:234-235`). Timeout → `OmieTimeoutError`
  (504). Timeouts: `OMIE_TIMEOUT_SECONDS=15`, `OMIE_TIMEOUT_EXTRATO_SECONDS=60`,
  `OMIE_TEST_CONNECTION_TIMEOUT_SECONDS=10` (`app/core/config.py:150-159`).
- **Log é metadado.** `module`/`endpoint`/`call`/`duration_ms`/`fault_code`; nunca o
  body (o `cObs` e o extrato carregam PII, §4.5).

Credencial em claro só existe em `app/modules/clients/omie_factory.py`
(`build_omie_client`, `:35` — decifra e devolve o client) e no "Testar conexão"
(`app/modules/clients/service.py:355`). O factory troca para `MockOmieClient` quando a
`app_key` começa com `FAKE_DEMO_OMIE_` (`omie_factory.py:81`).

```bash
grep -rln "omie.com.br\|OMIE_BASE_URL" apps/api/app --include=*.py   # esperado: client.py, schemas.py, core/config.py — nada mais
grep -n "_RETRYABLE_OMIE_API_ERROR_PREFIXES" -A 4 apps/api/app/integrations/omie/client.py
```

## Passo 4 — Rate limit: 1 requisição por MÉTODO por app_key

A Omie processa uma requisição do mesmo método por vez (`X-Omie-ParallelRateLimit:
1/4`); a segunda cai em `1880` e, insistindo, em `6 - Consumo redundante` com cooldown
de ~58 s. O código já respeita de dois jeitos — copie o padrão em vez de paralelizar:

- `fetch_pending` intercala pagar/receber com 1,5 s entre chamadas
  (`app/modules/reconciliations/processing/omie_fetch.py:39` e `:173-178`) — duas
  chamadas do MESMO endpoint nunca ficam adjacentes.
- `populate_from_extrato` serializa por cliente com um `asyncio.Lock`
  (`app/integrations/omie/lancamento_cache.py:256-257`) — foi a colisão de
  `ListarExtrato` concorrentes da Tela de Revisão que rendia "—" (86e33bmkb).
- O lote de escrita vai em sequência, teto `OMIE_POSTING_MAX_BATCH=50`
  (`config.py:209`).

```bash
grep -n "_INTER_CALL_DELAY_SECONDS\|call_plan" apps/api/app/modules/reconciliations/processing/omie_fetch.py
grep -n "_populate_locks" apps/api/app/integrations/omie/lancamento_cache.py
```

## Passo 5 — A nomenclatura MISTA de status (a que mais custa tempo)

- **Canônico** (DB, matcher, anomalias) vem de `ListarExtrato.cSituacao`, camelCase:
  `Conciliado`, `Atrasado`, `Previsto` (`OmieEntryStatus`, `schemas.py:70`). ⚠️ O campo
  é `cSituacao` — `cStatus` era o nome inventado da v1 (`:162`). E é **opcional**:
  lançamento recém-criado volta sem ele (`:198-208`), vira `""` e não dispara regra.
- **Filtro** `filtrar_por_status` de `ListarContasPagar/Receber` usa o enum oficial
  UPPERCASE: `ATRASADO`, `AVENCER` (`OmieTituloStatus`, `:83`). `"PREVISTO"` NÃO existe
  como filtro → `5001` (prod, 19/05/2026). Mapeamento único:
  `_TITULO_STATUS_TO_CANONICAL` (`omie_fetch.py:53-56`) — `AVENCER → Previsto`.
- O `status_titulo` da RESPOSTA de títulos veio UPPERCASE por extenso na fixture
  (`PAGO`, `RECEBIDO`) e não é usado para classificar: o canônico vem do FILTRO da
  chamada, não desse campo.

```bash
grep -n "class OmieEntryStatus\|class OmieTituloStatus\|cSituacao\|PREVISTO" apps/api/app/integrations/omie/schemas.py
grep -n "_TITULO_STATUS_TO_CANONICAL" -A 3 apps/api/app/modules/reconciliations/processing/omie_fetch.py
```

## Passo 6 — Sinal, tipo de conta e linhas de saldo

- **Extrato de conta corrente (doc):** `nValorDocumento` absoluto + `cNatureza` `'D'`
  (negativo) / `'C'` (positivo).
- **Extrato de CARTÃO (fixture real 21/08/2026, `cCodTipo='CR'`):** natureza `'P'`/`'R'`
  com valor JÁ sinalizado (79 `P`, 1 `R` na fixture). `LancamentoExtrato.signed_amount`
  (`schemas.py:269-281`) inverte SÓ `'D'` — inverter qualquer outra natureza quebra o
  cartão (CLAUDE.md §5.6).
- **Títulos:** o sinal é convenção do ADL, não da Omie — pagar = `-abs(valor)`
  (`omie_fetch.py:194-195`), receber = positivo.
- **Tipo de conta** (`tipo_conta_corrente`): `CC` conta corrente, `CR` cartão, **`CA` =
  Conta Aplicação, NÃO cartão** (`OmieAccountType`, `schemas.py:58-60`; o PRD erra
  isso).
- **Linhas de saldo:** `listaMovimentos` mistura linhas-resumo sem
  `nCodLancamento`/`cNatureza`/`cSituacao` (32 das 112 linhas da fixture).
  `listar_extrato` filtra ANTES do parse (`client.py:675`); qualquer consumidor novo do
  array cru precisa do mesmo filtro.

```bash
grep -o '"cNatureza": "[A-Z]"' apps/api/tests/fixtures/omie/listar_extrato.response.json | sort | uniq -c   # cartão: só P/R
grep -n "def signed_amount" -A 12 apps/api/app/integrations/omie/schemas.py
```

## Passo 7 — Títulos devolvem só CÓDIGO; nome resolve em runtime

`ListarContasPagar/Receber` devolve `codigo_cliente_fornecedor` e `codigo_categoria` —
nenhum nome (a fixture não tem `razao_social`/`nome_fantasia`). Por isso a afinidade de
fornecedor do matcher é sempre 0 para título (§5.5) e o snapshot persiste só o código
(`supplier_code`/`category_code`, §4.5). O nome é resolvido na hora e vive só em cache
TTL:

- categoria → `listar_categorias` (`client.py:602`) + `categorias_cache.py` (6 h, `:41`);
- fornecedor → `consultar_cliente` (`client.py:530`) + `clientes_cache.py` (6 h +
  negativo 15 min para código que a Omie disse não conhecer, `:31-34`);
- lançamento do extrato → `lancamento_cache.py` (2 h + negativo 15 min, `:47-55`).

Persistir nome, CNPJ ou descrição em claro é violação da §4.5 — código pode, nome não.

```bash
grep -c '"razao_social"\|"nome_fornecedor"' apps/api/tests/fixtures/omie/listar_contas_pagar.response.json   # esperado: 0
grep -n "DEFAULT_TTL_SECONDS\|UNRESOLVED_TTL_SECONDS" apps/api/app/integrations/omie/*_cache.py
```

## Passo 8 — ESCRITA: existe UMA, e qualquer outra é pergunta, não código

O invariante "Omie read-only" acabou na Sprint 7 com **`IncluirLancCC`**
(`financas/contacorrentelancamentos`) — lançamento de compras `sem_omie` da fatura de
CARTÃO na própria conta do cartão. ⚠️ A spec desta skill (14/08) falava em
`IncluirContaPagar`: não é usado e não está aprovado. Os 8 métodos que o ADL chama, e o
único `Incluir*`:

```bash
grep -rn 'call_name="' apps/api/app --include=*.py | grep -v "call_name=call_name"   # 8 linhas; só IncluirLancCC escreve
```

O que protege essa escrita (CLAUDE.md §3.16; tudo em
`app/modules/reconciliations/omie_posting/`):

- **Kill-switch `OMIE_POSTING_ENABLED`**, default `False` (`config.py:196`) —
  `_require_enabled` (`service.py:263`) → 409 `OmiePostingDisabledError`. Ligar é por
  ambiente, via `--update-env-vars`.
- **Só cartão**: `_require_credit_card` (`:269`) — `account_type == 'credit_card'` = `CR`.
- **Estorno bloqueado**: valor positivo → `estorno_nao_verificado` (`_eligibility_block`,
  `:717-740`). O contrato não tem `cNatureza`; o crédito segue sem captura.
- **Dedup é do ADL**: intenção em `reconciliation_omie_postings` ANTES do POST (`_send`,
  `:391-415`), chave `cCodIntLanc` derivada da IDENTIDADE da linha (`keys.py:61`), nunca
  do conteúdo. A Omie provou idempotência sobre a chave (2º POST devolve o mesmo
  `nCodLanc`) — é defesa secundária.
- **`faultstring` = falha definitiva**, nada marcado como lançado; a mensagem do
  provedor é persistida e **nunca logada** (`:427-451`). **Timeout nunca reenvia às
  cegas**: `_reconcile` (`:539-578`) é sempre inconclusivo porque o extrato NÃO devolve
  `cCodIntLanc` (0 ocorrências nas fixtures).
- **Contrato verificado em 21/08/2026**: `param` aninhado, `nValorLanc` NÚMERO JSON
  (serializer `schemas.py:514-525`; string deu `3102`), `cTipo='DIN'` obrigatório na
  prática, formato plano recusado com `5001`. Gate: `TestIncluirLancCCRealFixture`
  (`test_omie_fixtures.py:183`).

**Escrita nova (outro `Incluir*`, qualquer `Alterar*`/`Excluir*`, ou `IncluirLancCC`
fora do fluxo de cartão) → PARE e pergunte ao usuário** (`AskUserQuestion`, CLAUDE.md
§6.1/§6.21) antes de uma linha de código. Se aprovado, o mínimo não-negociável: captura
opt-in com `OMIE_CAPTURE_ALLOW_WRITE=1` + `OMIE_CAPTURE_COD_INT_LANC` (limpeza manual no
Omie depois — `ExcluirLancCC` não existe no ADL), fixture request+response+repeat+
readback, gate de fixture verde, kill-switch próprio default `False`, tabela de intenção
antes do POST.

```bash
grep -n "OMIE_POSTING_ENABLED" apps/api/app/core/config.py apps/api/app/modules/reconciliations/omie_posting/service.py
grep -n "estorno_nao_verificado\|_require_credit_card\|inconclusive" apps/api/app/modules/reconciliations/omie_posting/service.py
```

## Passo 9 — Como testar sem a Omie

- **Unit do client:** `respx` sobre a URL canônica — `tests/unit/test_omie_client.py`
  (`_omie_url`, `:204`; `@respx.mock` + `respx.post(_omie_url("financas", "extrato"))`).
  Cenários já cobertos e copiáveis: fault de auth, fault genérico, 5xx retryable
  (`1880`, `8020`), 5xx permanente, timeout.
- **Fluxo local ponta a ponta:** `MockOmieClient` (`app/integrations/omie/mock_client.py:306`)
  entra sozinho quando a credencial do cliente começa com `FAKE_DEMO_OMIE_` —
  cliente-demo de `scripts/seed_demo_client.py`. Nunca em produção.
- **Contrato:** o gate de fixtures do passo 1. Mock escrito à mão com os nomes que você
  assume NÃO conta.

```bash
cd apps/api && uv run --extra dev pytest tests/unit/test_omie_client.py tests/unit/test_omie_fixtures.py -q --no-cov   # 45 passed em 10/09/2026
```

## Pontos em aberto — não redescubra, feche com captura

1. **Paginação de `ListarExtrato`.** Doc incompleta. Evidência atual: a fixture real
   (jul/2026, cartão) NÃO traz `pagina`/`total_de_paginas` —
   `test_listar_extrato_has_no_pagination` (`test_omie_fixtures.py:109`) trava isso — e
   o código assume resposta única com timeout de 60 s. **Falta:** provar que não há
   truncamento silencioso: capturar conta com centenas de movimentos no mês e comparar
   `len(listaMovimentos)` com o total que a UI da Omie mostra; validar com o Galhardo
   (CLAUDE.md §10).
2. **`filtrar_por_status` aceita CSV?** A doc diz `"AVENCER,ATRASADO"`; o código faz 4
   chamadas separadas (`client.py:731-734`, `omie_fetch.py:146`). **Falta:** uma captura
   com o CSV e conferir (a) sem `5001` e (b) `total_de_registros` = soma das duas
   chamadas separadas. Fechar permite cortar `fetch_pending` pela metade.
3. **Saldo em data específica** (fallback de `balance_start`, S10). Pista nova: o
   envelope real do extrato traz `nSaldoAnterior`, `nSaldoAtual`, `nSaldoConciliado`,
   `nSaldoProvisorio`, `nSaldoDisponivel` — e o código ignora todos. **Falta:**
   confirmar a semântica (saldo em `dPeriodoInicial − 1`? conciliado ou provisório?)
   contra a UI da Omie em 2 datas e decidir se alimenta
   `app/modules/reconciliations/processing/balances.py:106-108` quando o arquivo não
   traz saldo.

```bash
grep -rn "nSaldoAnterior" apps/api/app --include=*.py   # esperado hoje: nada — o envelope traz, o código ignora
```

## Fechamento

Rode a skill `gate` e feche com a skill `entrega`. Mudou campo, envelope ou método do
Omie? A fixture correspondente é recapturada na MESMA entrega, e os §3.16/§5.6/§5.7 do
CLAUDE.md são conferidos contra o que a captura mostrou (§13).
