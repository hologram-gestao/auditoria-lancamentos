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

---

# Captura de ESCRITA — `IncluirLancCC` (BACK 07.1)

> ⚠️ **S-1 (ASSUMIDA — NÃO TESTADA / RISCO).** O contrato de escrita usado pela
> Sprint 7 — nomes de campo, a convenção de sinal (`nValorLanc` **absoluto** +
> `cNatureza` carregando o sinal) e a **unicidade de `cCodIntLanc`** — veio da
> **doc oficial**, não de uma resposta real. É o mesmo formato do defeito P11 da
> Sprint 1 (contrato de paginação fabricado, implementado contra um mock que
> repetia a invenção). **Nada da Sprint 7 pode ser declarado verde sem esta
> captura.**

## ⚠️ Por que esta captura é opt-in

**A Omie não tem sandbox** (CLAUDE.md §10) — e, diferente da leitura, capturar
`IncluirLancCC` **cria um movimento financeiro real na contabilidade de um
cliente**. Conflito registrado, não resolvido por conta própria: o DoD do PRD
pede "validado em sandbox real"; sandbox não existe, então a captura roda contra
**conta real** (Quial) numa conta corrente que o operador aceite sujar, e o
lançamento é **excluído manualmente depois**.

Por isso a captura de escrita:

- só roda com `OMIE_CAPTURE_ALLOW_WRITE=1` (`1`/`true`/`yes`; qualquer outra
  coisa é NÃO). Sem a variável, o script **não faz POST algum** e avisa;
- usa valor mínimo (`0.01` por default) e `cObs` que identifica a origem;
- exige `OMIE_CAPTURE_COD_INT_LANC` explícito — é por essa chave que você
  localiza o lançamento no Omie para excluir.

## Cross-check da doc oficial (19/08/2026) — leia antes da captura de escrita

Duas leituras independentes de
`https://app.omie.com.br/api/v1/financas/contacorrentelancamentos/` concordam:
a doc descreve o `param` do `IncluirLancCC` **aninhado** (`cCodIntLanc` no
topo; `cabecalho` com `nCodCC`/`dDtLanc`/`nValorLanc`; `detalhes` com
`cCodCateg`/`cTipo`/`cObs`/...), **sem `cNatureza`** no contrato de escrita —
e o nosso DTO emite tudo **plano**, com `cNatureza`. A resposta, ao contrário,
bate 1:1 (`nCodLanc`, `cCodIntLanc`, `cCodStatus`, `cDesStatus`).

Consequência prática: **a recusa do 1º POST é o desfecho esperado, e é captura
VÁLIDA** — o script grava a `faultstring` verbatim em
`incluir_lanc_cc.response.json` e para (nada foi criado; sem 2º POST, sem
readback). Só se a Omie aceitar o formato plano é que a sequência completa
(idempotência + readback) roda. O DTO não foi reescrito para o formato
aninhado de propósito: a doc desta API já errou 3x neste repositório, e a
faultstring real decide melhor que uma segunda suposição. Atenção: se a
exceção for **timeout** (não faultstring), confira no Omie se o lançamento
chegou a ser criado antes de re-rodar.

## Como capturar (operador com credencial autorizada)

```bash
cd apps/api
# ... as mesmas variáveis da captura de leitura, mais:
export OMIE_CAPTURE_ALLOW_WRITE=1
export OMIE_CAPTURE_COD_CATEG=...            # cCodCateg VÁLIDO no cliente
export OMIE_CAPTURE_COD_INT_LANC=ADL0701CAP1 # <= 20 chars; anote para excluir depois
export OMIE_CAPTURE_DATA_LANC=01/04/2026     # opcional (default: PERIODO_INICIAL)
export OMIE_CAPTURE_VALOR_LANC=0.01          # opcional (default: 0.01)
uv run python -m scripts.capture_omie_fixtures
```

### Os DOIS POSTs do mesmo `cCodIntLanc`

O script faz o POST **duas vezes com a mesma chave** e grava as duas respostas.
Isso é obrigatório: **uma resposta só (1 POST) mostra o contrato, mas não
demonstra idempotência.** Arquivos gerados:

| Arquivo                                | O que prova                                                         |
| -------------------------------------- | ------------------------------------------------------------------- |
| `incluir_lanc_cc.request.json`         | as chaves que a Omie **aceitou** (sem `app_key`/`app_secret`)       |
| `incluir_lanc_cc.response.json`        | o formato da resposta (inclusive o nome real do `nCodLanc`)         |
| `incluir_lanc_cc_repeat.response.json` | se o 2º POST foi **recusado** (unicidade) ou criou outro lançamento |
| `incluir_lanc_cc.readback.json`        | o extrato do dia — **evidência da convenção de sinal**              |

Sobre o `repeat`: se a Omie recusar (o esperado), ela responde **HTTP 200 com
`faultstring`** e o `OmieClient` levanta exceção — o script grava a mensagem
verbatim num envelope `_adl_capture_*`. Isso conta como prova de unicidade.
Se o 2º POST **criar um segundo lançamento**, S-1 está **refutada**: o teste
`test_repeat_post_documents_idempotency` falha e a dedup do ADL (BACK 07.2)
passa a ser a única defesa — **exclua os dois lançamentos**.

Sobre o `readback`: o request gravado só mostra o que **mandamos**. Quem
responde "o Omie entendeu `cNatureza='D'` + valor absoluto como débito?" é o
extrato relido — por isso ele faz parte da captura.

## Antes de commitar — sanitização e limpeza (escrita)

1. **Segredos:** conferido automaticamente por
   `TestFixturesCarryNoSecrets::test_no_request_fixture_contains_credentials` —
   nenhuma fixture pode conter `app_key`/`app_secret`.
2. **PII:** anonimize os **VALORES** (nomes, CNPJ) mantendo **NOMES DE CAMPO e
   estrutura verbatim**. Não altere chaves, tipos nem o envelope — são os nomes
   que o teste verifica. Não mexa em `nCodLanc`/`nCodLancamento`: o teste do
   readback casa o ID criado com a linha do extrato.
3. **AÇÃO MANUAL OBRIGATÓRIA — excluir o lançamento no Omie:** entre no Omie →
   Finanças → Conta Corrente → a conta usada em `OMIE_CAPTURE_CONTA_ID` → filtre
   pela data de `OMIE_CAPTURE_DATA_LANC` → localize o lançamento de
   `R$ 0,01` com a observação `ADL BACK 07.1 - captura de fixture` (chave de
   integração = `OMIE_CAPTURE_COD_INT_LANC`) → **excluir**. Se o 2º POST tiver
   criado um segundo lançamento, exclua **os dois**. O ADL não implementa
   `ExcluirLancCC` (fora do escopo da Sprint 7) — a limpeza é manual.

## Nunca

- **Não fabrique fixture.** Um JSON escrito à mão com os nomes que assumimos
  confirma a invenção — é literalmente o defeito P11. Sem fixture, os testes
  SKIPAM citando S-1, e isso é o comportamento correto.
- **Não copie a doc oficial para dentro de um mock** e chame de verificado.
- **Não commite credencial nem PII** nos JSONs.
