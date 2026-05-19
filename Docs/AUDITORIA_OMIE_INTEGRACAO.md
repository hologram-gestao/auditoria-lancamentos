# Auditoria — Integração Omie

> **Status:** 🔴 Pendente de correção — 5 CRÍTICOS, 3 ALTOS, 3 MÉDIOS, 2 BAIXOS.
> **Data da auditoria:** 2026-05-19.
> **Auditor:** Claude (read-only, cruzando código vs. doc oficial Omie em `app.omie.com.br/api/v1/...`).
> **Trigger:** após o bug histórico de `ListarContasCorrentes` (commit `168a495`),
> em que a doc interna descrevia campos errados e o CI permaneceu verde por meses
> porque o mock espelhava a interpretação errada do schema.

---

## Como retomar este documento

Quando voltar a trabalhar nesta auditoria:

1. **Não comece pelos fixes de schema.** Comece por capturar **uma response REAL** do
   Omie para cada um dos 4 endpoints quebrados (ver §"Pré-requisito" abaixo).
   Sem fixtures reais, qualquer correção corre o risco de só trocar
   "schema-errado-mas-internamente-consistente" por "outro schema-errado-mas-
   internamente-consistente" — exatamente como aconteceu na v1 deste projeto.
2. **Ordem de ataque sugerida** (ver §"Plano de execução" no fim):
   - S5.fix-a: fixtures reais + golden tests por endpoint (pré-requisito).
   - S5.fix-b: corrigir `ListarExtrato` (chave + campos).
   - S5.fix-c: corrigir `ListarContasPagar/Receber` (chave + filtro + status).
   - S5.fix-d: alinhar enums (`OmieAccountType`, `OmieTituloStatus`).
   - S5.fix-e: revisar timeout per-endpoint + auth faultcode.
3. **Pendências [?]** — todas listadas em §"Pendências (perguntar ao Galhardo)".
   Idealmente resolver TODAS antes de codar.
4. **Atualizar a doc interna** `Docs/documentation/6. Integração com API do Omie-*.md`
   na mesma PR — hoje ela contradiz a doc oficial em 4 dos 5 endpoints, e
   reintroduzir os bugs é fácil enquanto a doc interna estiver errada.

---

## Pré-requisito (antes de qualquer fix)

Criar um script de captura de fixtures (ex.: `scripts/capture_omie_responses.py`)
que, com uma credencial Omie real (Quial em dev), faça **uma chamada de cada
endpoint** e persista o JSON cru em `tests/fixtures/omie/`:

- `tests/fixtures/omie/listar_clientes.json`
- `tests/fixtures/omie/listar_contas_correntes.json`
- `tests/fixtures/omie/listar_extrato.json`
- `tests/fixtures/omie/listar_contas_pagar_atrasado.json`
- `tests/fixtures/omie/listar_contas_pagar_avencer.json`
- `tests/fixtures/omie/listar_contas_receber_atrasado.json`

Depois, escrever 1 teste por endpoint que carregue a fixture e rode
`Model.model_validate(item)` para **cada** item do array. Esses testes substituem
a confiança hoje depositada no `MockOmieClient` (que pula o `model_validate`
inteiramente).

**Regra de ouro:** nunca commitar credenciais; o script deve ler de `.env` local
e o JSON capturado deve ser **scrubbed** (substituir `app_key`/`app_secret` e
quaisquer dados sensíveis do cliente real Quial por placeholders antes de
commitar). Validar manualmente antes do `git add`.

---

## Endpoints consumidos pelo código

| Módulo     | Endpoint        | Call                    | Caller                                                                                        |
| ---------- | --------------- | ----------------------- | --------------------------------------------------------------------------------------------- |
| `geral`    | `clientes`      | `ListarClientes`        | [apps/api/app/integrations/omie/client.py:355](apps/api/app/integrations/omie/client.py#L355) |
| `geral`    | `contacorrente` | `ListarContasCorrentes` | [client.py:368](apps/api/app/integrations/omie/client.py#L368)                                |
| `financas` | `extrato`       | `ListarExtrato`         | [client.py:397](apps/api/app/integrations/omie/client.py#L397)                                |
| `financas` | `contapagar`    | `ListarContasPagar`     | [client.py:424](apps/api/app/integrations/omie/client.py#L424)                                |
| `financas` | `contareceber`  | `ListarContasReceber`   | [client.py:443](apps/api/app/integrations/omie/client.py#L443)                                |

Não há nenhum `IncluirX`/`AlterarX`/`UpsertX` consumido — só leitura.
Não há outras chamadas Omie fora de `client.py`/`mock_client.py`.

---

## Achados por severidade

### [CRÍTICO-1] `financas/extrato` — `ListarExtrato` — chave do array errada (lista sempre vazia)

- **Sintoma:** `listar_extrato()` sempre retorna `[]` em produção. Pior: sem erro,
  sem warning, sem fault — Pydantic nem chega a ser invocado porque
  `resp.get("extrato")` cai no fallback `or []`. Toda sessão de conciliação real
  vai cruzar contra zero entradas Omie, gerando 100 % das movimentações como
  `sem_omie` e 100 % das anomalias como falso-positivo `missing_in_omie`.
  **Caso idêntico ao bug histórico de `ListarContasCorrentes`.**
- **Esperado pela Omie:** o envelope `eccListarExtratoResponse` traz o array de
  movimentos sob a chave **`listaMovimentos`** (no mesmo nível dos campos de
  saldo). Não existe chave `extrato`.
  Fonte: <https://app.omie.com.br/api/v1/financas/extrato/>.
- **Atual no código:** [apps/api/app/integrations/omie/client.py:405](apps/api/app/integrations/omie/client.py#L405)
  ```python
  raw_items: list[dict[str, Any]] = resp.get("extrato") or []
  ```
- **Risco:** silencioso, derruba **todo o produto** assim que um cliente real
  for processado. Nenhum teste pega — `mock_client.listar_extrato` devolve
  `LancamentoExtrato` já construído, pulando inteiramente o caminho de
  `call()`+parsing.
- **Fix:**
  - trocar para `resp.get("listaMovimentos") or []`;
  - adicionar teste com fixture do response REAL (ver §Pré-requisito);
  - considerar logar `unknown_response_keys` quando a lista vier vazia mas o
    envelope tiver chaves desconhecidas — daria alerta rápido em divergências
    futuras.

---

### [CRÍTICO-2] `financas/extrato` — `ListarExtrato` — TODOS os nomes de campo errados

- **Sintoma:** mesmo se o CRÍTICO-1 fosse corrigido, **nenhum** item passaria
  pelo `LancamentoExtrato.model_validate(...)` — TODOS os 6 aliases estão errados.
  Resultado: `ValidationError` em massa para cada lançamento.
- **Esperado pela Omie** (fonte: <https://app.omie.com.br/api/v1/financas/extrato/>):

  | Campo no código (alias) | Campo REAL do Omie                                                                      |
  | ----------------------- | --------------------------------------------------------------------------------------- |
  | `nCodLanc`              | `nCodLancamento`                                                                        |
  | `dDtLanc`               | `dDataLancamento`                                                                       |
  | `nValorLanc`            | `nValorDocumento`                                                                       |
  | `cDescrLanc`            | _não existe_ — usar `cObservacoes` ou `cDocumentoFiscal`/`cNumero` (decisão de produto) |
  | `cCateg`                | `cCodCategoria` (código) ou `cDesCategoria` (descrição)                                 |
  | `cFornecedor`           | `cRazCliente` (razão social) ou `cDesCliente` (descrição)                               |
  | `cStatus`               | `cSituacao`                                                                             |

  Outros campos relevantes não consumidos hoje, mas que podem mudar a lógica de
  matching: `cTipoDocumento`, `nCodCliente`, `nCodLancRelac` (relaciona com
  título a pagar/receber — pode trocar a heurística de matching atual!),
  `cParcela`, `cNumero`, `cDocumentoFiscal`, `nSaldo`, `nSaldoPrev`.

- **Atual no código:** [apps/api/app/integrations/omie/schemas.py:102-118](apps/api/app/integrations/omie/schemas.py#L102-L118) — toda a classe `LancamentoExtrato`.
- **Risco:** ValidationError 100 % dos lançamentos.
- **Fix:**
  - reescrever `LancamentoExtrato` campo a campo contra a doc oficial;
  - revisar `OmieLancamentoData.from_lancamento` em [lancamento_cache.py:126-142](apps/api/app/integrations/omie/lancamento_cache.py#L126-L142) — `item.c_descr_lanc`, `item.c_categ`, `item.c_fornecedor`, `item.c_status` vão mudar de nome (e de semântica: o "fornecedor" passa a ser `cRazCliente`, que vem com razão social inteira);
  - **decisão pendente:** queremos `cCodCategoria` (legível tipo `"DT - IOF"`) ou `cDesCategoria` (descrição cheia)? Idem `cRazCliente` vs `cDesCliente`? Perguntar ao Pedro/Leonardo;
  - capturar response real para fixture antes de implementar (ver §Pré-requisito).

---

### [CRÍTICO-3] `financas/contapagar` — `ListarContasPagar` — nome do filtro errado + chave do array errada

- **Sintoma:** dois bugs combinados.
  - **(a)** O parâmetro `filtrar_por_conta_corrente` provavelmente é silenciosamente
    ignorado pelo Omie (não reconhece o nome) — a chamada acaba listando contas
    a pagar de **todas as contas correntes** do cliente, não só da conta da sessão.
  - **(b)** A chave `cadastro` no response não existe — Omie usa `conta_pagar_cadastro`.
    Como `_paginate` faz `resp.get(list_key) or []`, recebe sempre lista vazia →
    ninguém percebe que o filtro está errado, porque "vazio" é a saída final.

  Resultado em produção: zero títulos a pagar fluindo para detecção de
  `missing_in_file`, gerando relatório limpo e falsamente OK.

- **Esperado pela Omie** (fonte: <https://app.omie.com.br/api/v1/financas/contapagar/>):
  - Parâmetro: **`filtrar_conta_corrente`** (sem o `por_`), tipo integer.
  - Response: chave do array **`conta_pagar_cadastro`** dentro de `lcpListarResponse`. Envelope de paginação igual ao documentado (`pagina/total_de_paginas/registros/total_de_registros`).
- **Atual no código:**
  - [client.py:464](apps/api/app/integrations/omie/client.py#L464) — `"filtrar_por_conta_corrente": conta_corrente_id`
  - [client.py:472](apps/api/app/integrations/omie/client.py#L472) — `list_key="cadastro"`
- **Fix:**
  - renomear o parâmetro para `filtrar_conta_corrente`;
  - parametrizar `list_key` por endpoint (`_listar_titulos` precisa receber o
    key correto, já que pagar e receber usam keys diferentes — ver CRÍTICO-4);
  - mesmo padrão de teste com fixture real do achado anterior.

---

### [CRÍTICO-4] `financas/contareceber` — `ListarContasReceber` — nome do filtro errado + chave do array errada

- **Sintoma:** idêntico ao CRÍTICO-3 — `filtrar_por_conta_corrente` ignorado,
  `cadastro` inexistente; em produção retorna sempre `[]`.
- **Esperado pela Omie** (fonte: <https://app.omie.com.br/api/v1/financas/contareceber/>):
  - Parâmetro: **`filtrar_conta_corrente`** (sem `por_`).
  - Response: chave do array **`conta_receber_cadastro`** dentro de
    `lcrListarResponse`.
  - **Atenção:** essa chave é **diferente** da de `contapagar`
    (`conta_pagar_cadastro`), então o `_listar_titulos` atual, que hard-coda
    `list_key="cadastro"`, é estruturalmente incapaz de servir os dois endpoints
    com um único parâmetro fixo. Hoje "funciona" só porque ambos retornam vazio
    igual.
- **Atual no código:** [client.py:441-477](apps/api/app/integrations/omie/client.py#L441-L477).
- **Fix:**
  - mover `list_key` para argumento explícito do `_listar_titulos`
    (caller passa `"conta_pagar_cadastro"` ou `"conta_receber_cadastro"`);
  - renomear parâmetro de filtro.

---

### [CRÍTICO-5] `contapagar`/`contareceber` — campos `nome_fornecedor` e `descricao_categoria` não existem na response

- **Sintoma:** mesmo depois de corrigir o list_key, o parse via
  `TituloAPagarReceber.model_validate(...)` vai dar `ValidationError` se algum
  dia chegar dado real — porque `nome_fornecedor` e `descricao_categoria`
  **não constam** no response oficial de nenhum dos dois endpoints. Mas Pydantic
  os define como opcionais (`default=None`), então provavelmente nem erra:
  simplesmente **persiste tudo como `None` para sempre**.

  A aba de revisão Omie exibirá "—" no fornecedor e categoria, e o usuário não
  terá como identificar o título.

- **Esperado pela Omie** (fontes: <https://app.omie.com.br/api/v1/financas/contapagar/>,
  <https://app.omie.com.br/api/v1/financas/contareceber/>): cada item tem
  `codigo_cliente_fornecedor` (integer) e `codigo_categoria` (string20). Os
  nomes legíveis precisam ser resolvidos via outros endpoints
  (`ListarClientes`/`ListarFornecedores`/`ListarCategorias`) — **ou** ativando
  `exibir_obs="S"` (não testado).
- **Atual no código:** [schemas.py:147-148](apps/api/app/integrations/omie/schemas.py#L147-L148).
- **Fix:**
  - trocar campos para `codigo_cliente_fornecedor: int | None` e
    `codigo_categoria: str | None`;
  - adicionar resolução de nomes legíveis (lookup table de categorias +
    `ListarClientes`/`ListarFornecedores` em batch) — pode virar uma sessão
    dedicada (S15+);
  - **decisão pendente:** exibir só código ou pagar custo de extra round-trip
    ao Omie para resolver o nome? Conversar com produto.

---

### [ALTO-1] `contapagar`/`contareceber` — `filtrar_por_status="PREVISTO"` não consta nos valores documentados

- **Sintoma:** `OmieClient.listar_contas_pagar(status=OmieTituloStatus.PREVISTO)`
  envia `"PREVISTO"`. Esse valor **não aparece** na lista oficial de
  `filtrar_por_status` do `ListarContasPagar`. Comportamento real do Omie quando
  recebe um valor inválido é incerto — pode (a) retornar `faultstring`, (b)
  ignorar o filtro e devolver tudo, (c) retornar vazio. Qualquer um é problema.
- **Esperado pela Omie** (fonte: <https://app.omie.com.br/api/v1/financas/contapagar/>):
  valores válidos documentados —
  `CANCELADO, PAGO, LIQUIDADO, EMABERTO, PAGTO_PARCIAL, VENCEHOJE, AVENCER, ATRASADO`.
  **`PREVISTO` não está listado.** O equivalente semântico ("a vencer") é
  `AVENCER`.
- **Atual no código:** [schemas.py:46-50](apps/api/app/integrations/omie/schemas.py#L46-L50)
  ```python
  class OmieTituloStatus(StrEnum):
      ATRASADO = "ATRASADO"
      PREVISTO = "PREVISTO"
  ```
  e callers em [client.py:420-447](apps/api/app/integrations/omie/client.py#L420-L447)
  - `apps/api/app/modules/reconciliations/processing/omie_fetch.py:134/152`.
- **Fix:**
  - renomear enum para `AVENCER = "AVENCER"` (mantendo `ATRASADO`);
  - alinhar com produto se faz sentido também filtrar `VENCEHOJE` (vence-hoje,
    atualmente cai no buraco entre os dois);
  - validar com chamada real qual status é devolvido nos itens do response
    (campo `status_titulo` é `string3` na doc — ver ALTO-2).

---

### [ALTO-2] `contapagar`/`contareceber` — `status_titulo` no response é `string3` (não bate com o valor do filtro)

- **Sintoma:** o filtro `filtrar_por_status` aceita strings longas (`"ATRASADO"`,
  `"AVENCER"`), mas o campo `status_titulo` no response é declarado **`string3`**
  — sugere abreviação tipo `"ATR"`, `"AVC"`, `"PAG"`. O código compara
  `item.status_titulo == OmieTituloStatus.ATRASADO.value` (`"ATRASADO"`) em
  vários lugares — vai sempre dar falso.
- **Esperado pela Omie:** confirmar com chamada real qual o conteúdo de
  `status_titulo`. Doc oficial diz só "Status do Título" (string3). **[?]**
- **Atual no código:** [schemas.py:149](apps/api/app/integrations/omie/schemas.py#L149) + uso em `OmieTituloStatus` enum.
- **Fix:**
  - **[?]** capturar response real e confirmar formato antes de codar mais
    lógica condicional;
  - se for abreviado, criar dois enums distintos: `OmieTituloFilterStatus`
    (palavras inteiras, para `filtrar_por_status`) e `OmieTituloResponseStatus`
    (abreviações, para parse).

---

### [ALTO-3] `financas/extrato` — `ListarExtrato` não tem paginação documentada

- **Sintoma:** o endpoint não tem parâmetros `pagina`/`registros_por_pagina` e o
  response não tem envelope de paginação — `listaMovimentos` vem inteira numa só
  chamada. Se um cliente tiver um mês com 50k lançamentos, vamos receber um
  JSON gigante de uma vez (timeout, memória), ou o Omie corta silenciosamente
  em N itens.
- **Esperado pela Omie:** doc oficial não menciona limite máximo nem paginação.
  O ponto continua em aberto (`Docs/PLANO_IMPLEMENTACAO.md` §9).
- **Atual no código:** [client.py:376-406](apps/api/app/integrations/omie/client.py#L376-L406). TODO mencionado na docstring.
- **Fix:**
  - capturar nº de itens em log estruturado (`omie_extrato_size`) para ter
    telemetria assim que entrar em produção;
  - aumentar timeout default para `ListarExtrato` (sugestão: 60s, configurável
    separado de outros endpoints — hoje é global em `OMIE_TIMEOUT_SECONDS`);
  - perguntar diretamente ao suporte Omie / Galhardo se há corte automático.

---

### [MÉDIO-1] `geral/contacorrente` — `OmieAccountType.CREDIT_CARD = "CA"` está semanticamente errado

- **Sintoma:** o enum no código assume `"CA"` = Cartão de Crédito, mas segundo a
  doc oficial, **`CA` = "Conta Aplicação"**. **`CR` = "Cartão de Crédito"**.
  Toda lógica que depende do enum (ex.: "card é conciliável", filtros de UI,
  badge do front) classificará Conta Aplicação como Cartão e vice-versa.
- **Esperado pela Omie** (fonte: <https://app.omie.com.br/api/v1/geral/contacorrente/>):
  valores válidos de `tipo_conta_corrente`:
  > `AC` - Administradora de Cartões · `AD` - Adiantamento ·
  > **`CA` - Conta Aplicação** · `CC` - Conta Corrente · `CE` - Conta Empréstimo ·
  > `CG` - Conta Garantida · `CN` - Crediário / Carnê · `CP` - Conta Poupança ·
  > **`CR` - Cartão de Crédito** · `CV` - Carteira Virtual · `CX` - Caixinha ·
  > `MT` - Mútuo · `PG` - Conta de Pagamento.
- **Atual no código:**
  - [schemas.py:24-28](apps/api/app/integrations/omie/schemas.py#L24-L28) — enum.
  - [schemas.py:91](apps/api/app/integrations/omie/schemas.py#L91) — docstring `"'CC' (corrente), 'CA' (cartão), 'CX' (caixinha)"`.
  - [client.py:362](apps/api/app/integrations/omie/client.py#L362) — docstring `"Inclui contas tipo CC (corrente) e CA (cartão) — ambas conciliáveis."`
  - [mock_client.py:87-94](apps/api/app/integrations/omie/mock_client.py#L87-L94) — mock cria "Cartão Visa Empresarial" com `tipo_conta_corrente: "CA"` → vira Conta Aplicação no Omie real.
  - [Docs/documentation/6...md:41](Docs/documentation/6.%20Integra%C3%A7%C3%A3o%20com%20API%20do%20Omie-20260424133624.md#L41) — doc interna repete o erro (sobrevivente da v1 corrigida).
- **Fix:**
  - renomear enum: `CREDIT_CARD = "CR"`, **adicionar** `INVESTMENT = "CA"`
    (Conta Aplicação);
  - corrigir docstrings em `schemas.py`, `client.py`, doc interna `6...md`;
  - corrigir fixture do `mock_client.py` (a conta Visa deveria ser `"CR"`,
    não `"CA"`);
  - adicionar `_unsupported_account_type` warning no log quando aparecer um
    tipo fora do enum (resiliente a Omie adicionar tipos novos sem nos avisar).

---

### [MÉDIO-2] `client.py` — `_AUTH_FAULT_KEYWORDS` inclui `soap-env:client-101`, mas só inspeciona `fault_string`

- **Sintoma:** o keyword `"soap-env:client-101"` parece destinado ao **faultcode**
  (que normalmente é algo como `"SOAP-ENV:Client-101"`), mas `_raise_for_fault`
  só matcha contra `fault_string` (após `.lower()`). Erros de credencial cujo
  faultstring não contém literalmente uma das palavras (`"app_key"`,
  `"credenciais"`, etc.) — mas que vêm com faultcode `SOAP-ENV:Client-101` —
  vão cair em `OmieFaultError` em vez de `OmieAuthError`. UX: usuário vê
  "Ocorreu um erro ao acessar o Omie" em vez de "Credenciais Omie inválidas".
- **Esperado pela Omie:** **[?]** a doc oficial não enumera faultcodes.
  Convenção da comunidade aponta SOAP envelope codes (`SOAP-ENV:Client-101`,
  `-102`, `-103` …) mas precisa validar com Galhardo / chamada real com
  credencial errada.
- **Atual no código:** [client.py:58-68](apps/api/app/integrations/omie/client.py#L58-L68), [client.py:273-289](apps/api/app/integrations/omie/client.py#L273-L289).
- **Fix:**
  - separar `_AUTH_FAULT_STRING_KEYWORDS` (matched contra `fault_string.lower()`)
    e `_AUTH_FAULT_CODES` (matched exato contra `fault.fault_code`,
    case-insensitive);
  - registrar pelo menos uma fixture conhecida (deliberadamente quebrar
    credencial em dev e capturar o `{faultstring, faultcode}` real).

---

### [MÉDIO-3] `_paginate` — heurística `len(items) < page_size` pode iterar a mais

- **Sintoma:** o iterator para quando recebe menos itens que `page_size`, mas o
  Omie devolve `total_de_paginas` explicitamente em `lcpListarResponse`/
  `lcrListarResponse`/`ListarContasCorrentes`. Se em algum endpoint a última
  página vier exatamente cheia (caso raro mas possível), `_paginate` faz mais
  uma chamada desnecessária. Em uma conta com 100 títulos exatos, hoje fazemos
  2 requests Omie para o que cabia em 1.
- **Esperado pela Omie:** todos os endpoints listing têm `total_de_paginas` e
  `total_de_registros` no envelope.
- **Atual no código:** [client.py:295-340](apps/api/app/integrations/omie/client.py#L295-L340).
- **Fix:**
  - usar `total_de_paginas` quando disponível:
    `if pagina >= int(resp.get("total_de_paginas") or pagina): return`;
  - manter a heurística `len(items) < page_size` como fallback defensivo
    (cobre o `ListarContasCorrentes` legado que pode não vir com envelope
    sempre completo).

---

### [BAIXO-1] `mock_client.py` — fixtures de contas a pagar/receber não passam pelo `model_validate`

- **Sintoma:** o `MockOmieClient` instancia `TituloAPagarReceber(...)` direto,
  **pulando** o caminho `model_validate(raw)` do response real. Mesmo se os
  campos não-existentes (`nome_fornecedor`/`descricao_categoria`) forem
  corrigidos, esses mocks continuariam passando — porque eles têm os campos
  certinhos para o schema atual (errado).
- **Esperado:** mocks deveriam refletir a estrutura crua da API Omie e passar
  pelo mesmo parsing.
- **Atual no código:** [mock_client.py:195-235](apps/api/app/integrations/omie/mock_client.py#L195-L235).
- **Fix:**
  - reescrever fixtures como `dict` cru com chaves exatas do Omie (PascalCase /
    snake_case por endpoint) e passar por `TituloAPagarReceber.model_validate(...)`.
    Mesma coisa que já foi feito em `_MOCK_CONTAS`/`_MOCK_EXTRATO_ITAU` no caso
    do `ListarContasCorrentes`.

---

### [BAIXO-2] Doc interna `Docs/documentation/6...md` ainda contradiz a doc oficial

- A v2 da doc interna foi corrigida apenas para `ListarContasCorrentes`. Os
  demais endpoints ainda repetem as estruturas erradas do código
  (`extrato[]` com `nCodLanc/cFornecedor`, `cadastro[]` com `nome_fornecedor`,
  `filtrar_por_conta_corrente`, `tipo_conta_corrente="CA"=Cartão de Crédito`).
- **Risco:** a próxima pessoa lendo a doc interna escreve mais código errado
  (regression latente).
- **Fix:** atualizar a doc por endpoint, com link permanente para
  `https://app.omie.com.br/api/v1/{module}/{endpoint}/`, e mover a tabela de
  "alias do código → campo real" para ficar fácil de auditar.

---

## Code-smells transversais

- **Timeout único global de 15s** — `ListarExtrato` em produção (clientes
  maiores) pode estourar facilmente. Sugestão: per-endpoint timeout
  (extrato: 60s, demais: 15s).
- **Retries em endpoints idempotentes está OK** — só `ListarX` no escopo atual,
  tudo idempotente. Quando entrar `IncluirX`/`UpsertX` (não consumimos hoje),
  revisar.
- **Sem captura de fixtures reais** — toda divergência atual é fruto disso.
  Sugestão: criar `scripts/capture_omie_responses.py` (ver §Pré-requisito).
- **`_unknown_response_keys` warning ausente** — quando o Omie adicionar/renomear
  campos, vamos descobrir do mesmo jeito (cliente reclamando). Adicionar log
  estruturado quando o response contém chaves de envelope inesperadas, ou
  quando o tamanho da resposta excede X kb.

---

## Endpoints validados sem divergência

- **`geral/clientes` — `ListarClientes`**
  ([client.py:346-357](apps/api/app/integrations/omie/client.py#L346-L357)):
  chamada mínima (`pagina:1, registros_por_pagina:1`). Não consome nenhum campo
  do response além da ausência de `faultstring`. Doc oficial confirma os dois
  parâmetros e que a chave do array é `clientes_cadastro` — mas como o código
  nunca lê o array, isso é indiferente. **OK.**

---

## Pendências (perguntar ao Galhardo / capturar em dev)

- **[?] `cSituacao` (extrato)** — valores possíveis não documentados; código
  assume `Conciliado/Atrasado/Previsto` (em [schemas.py:38-43](apps/api/app/integrations/omie/schemas.py#L38-L43)). Precisa confirmar com chamada real.
- **[?] `status_titulo` (contapagar/receber)** — `string3`, formato desconhecido.
  Precisa confirmar com chamada real.
- **[?] Faultcodes auth** — provável `SOAP-ENV:Client-101/-102/-103` mas sem
  doc. Pedir ao Galhardo + capturar dev (forçar credencial errada e ler o
  `{faultstring, faultcode}`).
- **[?] `cExibirApenasSaldo` (extrato)** — efeito no formato da resposta não
  documentado. Não usamos hoje, mas se for ativado por engano altera o response.
- **[?] Limite real de page_size** em todos os endpoints listing (doc oficial
  não declara máximo). Hoje usamos 100 em `contacorrente` e 50 em `titulos` —
  pode aumentar?
- **[?] Paginação de `ListarExtrato`** — já constava no PLANO §9, mas
  permanece em aberto. Ver ALTO-3.
- **[?] `filtrar_por_status` aceita múltiplos valores?** — também já constava
  no PLANO §9. Se sim, podemos consolidar 2 chamadas em 1 por endpoint
  (`ATRASADO,AVENCER`).
- **[?] `nome do fornecedor` legível** — vem em `cRazCliente`/`cDesCliente` do
  extrato, mas em `codigo_cliente_fornecedor` (id) nos títulos. Confirmar com
  produto se vale o custo de resolver via batch lookup.

---

## Plano de execução (sessões sugeridas)

**Pré-condição para todas:** §Pré-requisito (fixtures reais capturadas e
golden tests verdes).

### S5.fix-a — Captura de fixtures + golden tests (1 dia)

- Script `scripts/capture_omie_responses.py` com `.env` local (Quial).
- Scrubbing de credenciais e dados sensíveis no JSON antes do commit.
- 6 fixtures (1 por endpoint × variações de status).
- 6 testes que rodam `Model.model_validate(item)` em cada item das fixtures.
- **DoD:** rodar `pytest tests/integrations/omie/test_fixtures.py` — todos
  vermelhos hoje (porque o schema atual não bate).

### S5.fix-b — `ListarExtrato` (1-2 dias)

- Reescrever `LancamentoExtrato` (schemas.py).
- Atualizar `OmieLancamentoData.from_lancamento` (lancamento_cache.py).
- Trocar `resp.get("extrato")` por `resp.get("listaMovimentos")` (client.py).
- Reescrever `_MOCK_EXTRATO_ITAU` para passar por `model_validate` com chaves reais.
- Atualizar doc interna `6...md` para refletir realidade.
- **DoD:** golden test de `listar_extrato.json` verde + `MockOmieClient.listar_extrato`
  continua devolvendo 8 itens (5 match + 3 órfãos) na demo da Padaria.

### S5.fix-c — `ListarContasPagar`/`Receber` (1-2 dias)

- Renomear `filtrar_por_conta_corrente` → `filtrar_conta_corrente`.
- Parametrizar `list_key` em `_listar_titulos` (`conta_pagar_cadastro` vs
  `conta_receber_cadastro`).
- Reescrever `TituloAPagarReceber` (codigo_cliente_fornecedor + codigo_categoria,
  sem nome_fornecedor/descricao_categoria).
- Decidir com produto: resolver nomes via batch lookup ou exibir códigos?
- Reescrever mocks de pagar/receber para passar por `model_validate`.
- Atualizar doc interna.
- **DoD:** golden tests verdes + MockOmieClient mantém comportamento da demo.

### S5.fix-d — Enums (½ dia)

- `OmieAccountType`: `CHECKING = "CC"`, `CREDIT_CARD = "CR"`, `INVESTMENT = "CA"`.
- `OmieTituloStatus`: `ATRASADO = "ATRASADO"`, `AVENCER = "AVENCER"`
  (eliminar `PREVISTO`).
- Decidir se separa `OmieTituloFilterStatus` vs `OmieTituloResponseStatus`
  depois de capturar `status_titulo` real.
- Atualizar callers em `omie_fetch.py` e `mock_client.py`.

### S5.fix-e — Hardening (½ dia)

- Timeout per-endpoint (`OMIE_TIMEOUT_EXTRATO_SECONDS` separado).
- `_AUTH_FAULT_CODES` separado de `_AUTH_FAULT_KEYWORDS`.
- `omie_unknown_response_keys` warning estruturado em `_do_call`.
- `omie_extrato_size` metric/log no `listar_extrato`.

### S5.fix-f — Doc interna (½ dia)

- Reescrever `Docs/documentation/6. Integração com API do Omie-*.md` por
  endpoint, com:
  - link permanente para a doc oficial;
  - tabela "alias do código → campo real do Omie";
  - lista de campos disponíveis vs campos consumidos;
  - status do que foi validado contra response real (data + commit).

---

## Resumo executivo

- **5 [CRÍTICO]** que quebram silenciosamente em produção: `ListarExtrato`
  (chave + 6 campos errados), `ListarContasPagar` e `ListarContasReceber`
  (filtro + chave do array errados, em paralelo), e o desencontro entre
  `nome_fornecedor`/`descricao_categoria` esperados vs `codigo_*` do Omie real.
  **Todos** do mesmo padrão do bug histórico de `ListarContasCorrentes`: mock
  isolado do parsing real, CI verde, descoberta só com cliente real.
- **3 [ALTO]** semânticos: `PREVISTO` não é status válido, `status_titulo` é
  `string3`, `ListarExtrato` sem proteção de tamanho.
- **3 [MÉDIO]** + **2 [BAIXO]**.
- **1 endpoint OK** (`ListarClientes`, porque mal lê o response).

**Recomendação operacional:** **antes de qualquer fix de campo**, capturar uma
response real do Omie para cada um dos 4 endpoints quebrados, persistir em
`tests/fixtures/omie/`, e exigir que cada `_listar_*` em `mock_client.py` passe
o dict por `model_validate`. Sem isso, qualquer correção corre o risco de só
trocar um schema-errado por outro schema-errado-mas-internamente-consistente.
