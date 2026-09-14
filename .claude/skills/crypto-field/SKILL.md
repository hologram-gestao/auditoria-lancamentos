---
name: crypto-field
description: >
  Roteiro OBRIGATÓRIO antes de persistir qualquer dado sensível novo e ao tocar
  app/core/crypto.py, crypto_service.py ou kms.py. Gatilhos literais: "campo cifrado",
  "criptografar", "dado sensível novo", "DEK", "KEK", "KMS", "AAD", "envelope",
  "dek_wrapped", "rotacionar chave", "indecifrável". A cripto é envelope com DEK por
  cliente e tem QUATRO landmines cujo custo é outage ou perda de dado IRREVERSÍVEL —
  e a primeira pergunta não é "como cifrar", é "esse dado precisa mesmo persistir?".
---

# /crypto-field — campo cifrado novo, sem pisar nas landmines

Área onde "o agent achou que sabia" sai mais caro. Números conferidos em 14/09/2026
contra o código: 11 campos cifrados, `CURRENT_ENVELOPE_VERSION = 1`, 3 suítes com
49 testes. Se um grep não bater, o código andou: releia o arquivo. Caminhos relativos
a `apps/api/`.

## Passo 1 — A pergunta que vem ANTES de cifrar: isso precisa persistir?

Três respostas possíveis, nesta ordem:

1. **Não persistir.** CNPJ, razão social, nome de fornecedor, descrição de categoria e
   nome de conta **não vão para o banco** — são buscados do Omie em tempo real e vivem
   só em cache TTL (CLAUDE.md §4.5). Se o dado novo cabe nessa lista, a resposta certa
   é buscar na hora, não cifrar. O que PODE persistir em claro é o **código**
   (`category_code`, `supplier_code`): código não é nome.
2. **Persistir em claro.** Valor monetário e data (§4.3/§4.4) — o SQL precisa deles
   para ordenar, filtrar e somar, e isoladamente não identificam ninguém.
3. **Cifrar.** Só o que é texto livre ou identificador do cliente final e precisa
   mesmo estar no banco: credencial, descrição de lançamento, nota do analista,
   contexto de anomalia, entrada de glossário, nome de arquivo.

Decidir errado aqui custa mais que qualquer detalhe de implementação: um campo em
claro que deveria ser cifrado é vazamento; um campo cifrado que deveria ser buscado
em runtime é dado do cliente parado no banco sem necessidade.

## Passo 2 — Como o envelope funciona (para não reinventar)

- Cada cliente tem uma **DEK** própria (`generate_dek`, 32 bytes, `core/kms.py:41`),
  guardada **embrulhada** em `clients.dek_wrapped`.
- A **KEK** faz wrap/unwrap e **nunca sai do KMS** em staging/prod
  (`CloudKmsClient`, `kms.py:115`, recurso em `KEK_KMS_KEY_NAME`). Em dev/test a KEK é
  derivada de `OMIE_ENCRYPTION_KEY` via HKDF (`LocalKmsClient`, `:63`, domínio
  `adl-kek:<key_id>`). Quem escolhe é `get_kms_client` (`:163`): `KEK_KMS_KEY_NAME`
  setado → Cloud KMS, senão local.
- O valor gravado é o envelope **`v<n>:<key_id>:<ciphertext_hex>`** na coluna
  `_encrypted` e o IV hex na coluna `_iv` (`crypto.py:207-215`).
- O **AAD** amarra o ciphertext à linha: `client_id ‖ tabela ‖ coluna ‖ pk`, separados
  por `\x1f` (`build_aad`, `crypto.py:156-158`). Copiar o ciphertext para outra linha,
  coluna ou cliente faz o decrypt FALHAR — é isso que impede um ciphertext de A ser
  lido em B.
- Leitura é **multi-chave**: linha sem o prefixo `v<n>:` é legado bare, decifrada com a
  chave global e **sem AAD** (`crypto.py:219-222`).

Os três pontos de entrada, em `core/crypto_service.py` — use estes, nunca monte um
`ClientCipher` na mão:

| Função                            | Quando             | O que faz                                                                      |
| --------------------------------- | ------------------ | ------------------------------------------------------------------------------ |
| `new_client_dek` (`:108`)         | criação de cliente | gera a DEK ANTES do INSERT e devolve `(cipher, dek_wrapped)`                   |
| `provision_client_cipher` (`:84`) | **escrita**        | garante DEK; se `dek_wrapped` é NULL, gera e seta in-place (o caller persiste) |
| `load_client_cipher` (`:63`)      | **leitura**        | só desembrulha; NÃO provisiona, não muta o cliente                             |

⚠️ O docstring de `ClientCipher` (`crypto.py:183`) manda usar `build_client_cipher`,
que **não existe** — é resquício de um nome antigo. Os nomes reais são os três acima.

```bash
grep -n "^async def \|^def " apps/api/app/core/crypto_service.py
grep -rn "ClientCipher(" apps/api/app --include=*.py | grep -v "core/crypto"   # esperado: nada fora do crypto_service
```

## Passo 3 — As QUATRO landmines (quebrar = outage ou perda irreversível)

1. **Nunca trocar `KEK_KEY_ID`** (hoje `k1`, `core/config.py:95-98`). O `key_id` gravado
   em cada linha é **descartado na leitura** (`crypto.py:224`, `_key_id` não é usado):
   o decrypt sempre desembrulha com a KEK ATUAL. E a rotação só provisiona DEK quando
   `dek_wrapped IS NULL` — **não existe caminho de re-embrulho no código**. Trocar o
   `KEK_KEY_ID` torna TODOS os clientes indecifráveis, sem volta.
2. **Manter `OMIE_ENCRYPTION_KEY` sempre setada** (`config.py:57`). Ela faz duas coisas:
   decifra o dado bare legado (`crypto.py:222`) e **deriva a KEK local** em dev/test
   (`kms.py:173`). Removê-la é outage imediato.
3. **Nunca alternar Cloud KMS ⇄ wrapper local** sobre um DB que já tenha DEKs do outro
   wrapper. São KEKs diferentes, o `dek_wrapped` de um não desembrulha no outro
   (`unwrap_dek` levanta `CryptoError`, `kms.py:109-112`), e não há migração no código.
   Em deploy isso é decidido por `KEK_KMS_KEY_NAME` — mudar essa variável num ambiente
   com dado é o mesmo erro.
4. **Alerting é fail-closed em staging/prod:** sem canal entregável o serviço **não
   sobe** (`verify_alert_config` no lifespan). E `ALERT_WEBHOOK_URL_SYNTHETIC` **não
   conta** como canal (`has_webhook_alert`/`has_alert_channel` em `config.py`) —
   contá-la deixaria subir em produção um serviço cujo único canal é o de teste, ou
   seja, alerta real sem para onde ir. Relevante aqui porque a falha de decifragem é
   justamente um dos alertas (`AlertCode.DECRYPT_FAILED`, `core/alerting.py:70`).

```bash
grep -n "KEK_KEY_ID\|KEK_KMS_KEY_NAME" apps/api/app/core/config.py
grep -rn "KEK_KEY_ID\|KEK_KMS_KEY_NAME" .github/workflows/deploy-dev.yml | head -3
```

## Passo 4 — Roteiro do campo novo (depois do passo 1 dizer "cifrar")

1. **Coluna par**: `<campo>_encrypted` (`Text`) + `<campo>_iv` (`String(IV_HEX_LENGTH)`).
   Nullable se o dado é opcional. Migration pela skill `migration`.
2. **Constante AAD nova** em `crypto_service.py`, no bloco das outras 11:
   `AAD_<ALGO> = ("<tabela>", "<coluna>_encrypted")`. ⚠️ **Esses pares são CONGELADOS**
   (`crypto_service.py:9-11`): renomear tabela ou coluna depois invalida a decifragem
   de tudo que já foi gravado. Escolha o nome uma vez.
3. **Cifrar** com `cipher.encrypt(texto, field_locator(AAD_<ALGO>, pk))` e gravar os
   dois retornos. O **IV é novo a cada operação** — `os.urandom(12)` dentro do
   `encrypt` (`crypto.py:210`), nunca reaproveitado, nunca passado de fora.
4. **A pk do AAD é a da PRÓPRIA linha.** Numa criação, gere o UUID ANTES do INSERT para
   compor o AAD (padrão em `clients/service.py:191-196`) — deixar o default do ORM
   resolver no flush daria AAD com pk errada.
5. **Entrar na lista canônica** da §4.1 do CLAUDE.md **na mesma entrega** (§13).
6. **Não precisa entrar no `scripts/rotate_encryption_key.py`.** Ele converte linhas
   **bare legadas** (pré-Sprint 3) e importa 7 das 11 constantes; campo criado depois
   da Sprint 3 nasce já no envelope `v1` e nunca tem linha bare. Se um dia existir
   re-embrulho de KEK (hoje não existe — landmine 1), aí sim todos entram.

```bash
grep -c "^AAD_" apps/api/app/core/crypto_service.py            # hoje 11
grep -n "_encrypted\b" apps/api/app/db/models/*.py | grep Mapped | wc -l   # hoje 11 colunas
```

## Passo 5 — Falha de decifragem tem DOIS regimes, e nenhum é silêncio

- **Caminho de credencial: levanta.** `build_omie_client` (`modules/clients/omie_factory.py:35`)
  propaga `CryptoError` (500 pelo handler). Nunca devolve texto, nunca segue com
  credencial vazia.
- **Tela de revisão e Excel: degrada visível.** O valor vira **`[indecifrável]`** com
  `logger.warning("export_decrypt_failed", field=...)` e contador
  (`export/service.py:717-722`), e o glossário marca `decrypt_failed=True` no schema
  (`modules/glossary/schemas.py:30,47`). Ao fim, o alerta `AlertCode.DECRYPT_FAILED`
  sai em `review/service.py:1106` e `export/service.py:221`.
- **Nunca célula silenciosamente vazia.** Um `except` que devolve `""` sem log e sem
  contador transforma perda de dado em relatório aparentemente normal — é o modo de
  falha que essa política existe para impedir. Em soma/contador, a linha indecifrável é
  pulada E contada (`totals.py:273-275`, warning `summary_decrypt_failed`).
- **DEK ausente nunca cai para a chave global.** `_require_dek` levanta
  (`crypto.py:200-205`); o fallback silencioso seria o defeito, não o remédio (travado
  por `test_decrypt_v_row_without_dek_raises_no_global_fallback`,
  `tests/unit/test_crypto_envelope.py:171`).

```bash
grep -rn "except CryptoError\|except Exception" apps/api/app/modules/<seu_modulo>/ | grep -A1 -i decrypt   # todo except loga e conta?
```

## Passo 6 — A race de DEK do rollout (incidente real, ainda aberta por construção)

`provision_client_cipher` (`crypto_service.py:95-97`) faz **read-modify-write de
`client.dek_wrapped` sem lock**. Dois writes concorrentes no PRIMEIRO uso de um cliente
legado geram DEKs diferentes: o dado cifrado com a DEK perdedora fica irrecuperável, e
aparece como `[indecifrável]` — perda de dado mascarada.

O que fecha a janela: **nenhum cliente com `dek_wrapped IS NULL`**. Cliente criado hoje
já nasce com DEK (`new_client_dek` antes do INSERT), então a race só alcança clientes
legados pré-Sprint 3 ainda não backfillados. Antes de qualquer rollout que toque
cripto, confira:

```sql
SELECT count(*) FROM clients WHERE dek_wrapped IS NULL;   -- esperado: 0
```

Se der > 0: rode `uv run python -m scripts.rotate_encryption_key` com escrita
quiescida (por lotes de 500, retomável, 2º run converte 0 — `scripts/rotate_encryption_key.py:82`)
antes de liberar o tráfego. Se precisar provisionar sob concorrência, o lock é seu
trabalho (`SELECT … FOR UPDATE` na linha do cliente ou advisory lock) — o código não o
tem hoje.

⚠️ Escrita em cliente **encerrado** é bloqueada ANTES do provisionamento
(`ClientClosedError`, padrão em `reconciliations/routes.py:355-359`): sem esse guard, o
provisionamento lazy re-embrulharia uma DEK nova num tenant cujo conteúdo já morreu por
crypto-shredding (§4.12).

## Passo 7 — Provar

```bash
cd apps/api && uv run --extra dev pytest tests/unit/test_crypto.py tests/unit/test_crypto_envelope.py tests/unit/test_crypto_service.py -q --no-cov
```

Medido em 14/09/2026: **49 passed em 0,42 s** (20 + 23 + 6). O que essas suítes já
cobrem, e que um campo novo herda de graça: round-trip, IV novo a cada chamada,
detecção de adulteração, **isolamento entre clientes** (`test_dek_of_a_cannot_decrypt_b`),
AAD por linha/coluna/tabela, coexistência com legado bare e DEK ausente. Campo novo
precisa de teste próprio só quando acrescenta política (ex.: o que a tela mostra quando
falha). Integração: `tests/integration/test_rotate_encryption_key.py`.

## Fechamento

Rode a skill `gate` e feche com a skill `entrega`. Campo cifrado novo **muda o
CLAUDE.md §4.1 na mesma entrega** (§13) — a lista canônica é o que o próximo agent vai
ler para saber o que é cifrado.
