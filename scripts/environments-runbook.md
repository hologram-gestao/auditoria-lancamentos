# Runbook do operador — provisionamento por ambiente (Sprint 3 + Sprint 11)

> Rodável para **dev E prod** (isolados: nunca compartilham KEK, secret nem serviço).
> Todo passo é **idempotente** ou **1× por ambiente**. Nada aqui derruba um deploy
> em andamento — os grants são best-effort; o único **gate** é o smoke de entrega
> do alerta (03.7), que é intencional.

## Ordem (por ambiente `<env>` ∈ {dev, prod})

### 1. Provisionar a infra versionada — `setup-gcp.sh` (idempotente)

```bash
bash scripts/setup-gcp.sh <env>
```

Cria/garante, sem duplicar:

- **KEK no Cloud KMS** (Req. 1 / 03.1): keyring `auditoria-<env>` + CryptoKey
  `omie-encryption-kek`, e o grant per-key `cloudkms.cryptoKeyEncrypterDecrypter`
  à SA de runtime. Imprime o `KEK_KMS_KEY_NAME` (resource path — **não** é segredo).
- **Secrets do canal de alerta** (Req. 4 / 03.7): `alert-webhook-url-<env>` e
  `alert-email-to-<env>` (container + versão **vazia** semeada + `secretAccessor`
  per-secret à SA de runtime).
- **Sincronização diária da carteira de títulos** (Sprint 11 R5 / 11.6):
  Cloud Run Job `auditoria-api-sync-titles-<env>` + Cloud Scheduler
  `auditoria-sync-titles-<env>` — detalhes no passo 5.

> ⚠️ Rode **antes** do 1º deploy: o `--update-secrets ...:latest` dos workflows
> falha se o secret não tiver versão.
>
> ⚠️ **Ordem dentro do ambiente:** a parte da **carteira** clona a configuração
> do job de `cleanup` (senão do de `migrate`) do MESMO ambiente. Num ambiente
> novo esses jobs ainda não existem: o script **avisa e segue** (não derruba
> nada), e você re-executa `setup-gcp.sh <env>` depois do 1º deploy. Nos dois
> primeiros passos (KMS/secrets) a ordem continua sendo "antes do deploy".

### 2. Configurar o canal de plantão real (valor não versionado)

Adicione o VALOR de **pelo menos um** canal entregável (senão o serviço é
**fail-closed** e nem sobe):

```bash
printf 'https://hooks.slack.com/services/XXX' \
  | gcloud secrets versions add alert-webhook-url-<env> --data-file=- --project=liberdade-assessoria
# (e-mail só entrega se ALERT_SMTP_HOST também estiver setado no serviço;
#  o webhook é o canal deliverable por padrão)
```

O destino é **sempre um endereço COMPARTILHADO da equipe de plantão** (grupo,
nunca uma pessoa — fator ônibus).

### 3. Bootstrap do smoke de ENTREGA (03.7) — admin de monitoração

O job `smoke-alert` (pós-deploy) **prova** a entrega: loga como um admin
dedicado e dispara o gatilho sintético da 03.6
(`POST /api/v1/system/alert-test`). Se a notificação não chegar (`delivered=false`),
o pipeline **reprova**.

1. **Crie um admin DEDICADO de monitoração** (perfil admin, senha forte,
   credenciais **não** versionadas — mesmo padrão do admin inicial):

   ```bash
   # exemplo — ajuste ao mecanismo de criação de usuário do ambiente
   SEED_ADMIN_EMAIL=smoke-monitor@hologram.com.br \
   SEED_ADMIN_PASSWORD='<senha-forte>' \
     <rodar o job/rotina de criação de usuário admin>
   ```

2. No GitHub → **Settings → Environments → `<development|production>` → Secrets**:

   | Secret                 | Valor                          |
   | ---------------------- | ------------------------------ |
   | `SMOKE_ADMIN_EMAIL`    | e-mail do admin de monitoração |
   | `SMOKE_ADMIN_PASSWORD` | senha dele (nunca no repo/log) |

3. Confirme a **Variable** `API_URL_<DEV\|PROD>` no mesmo Environment (base
   pública da API — a mesma que o build do web consome).

### 4. Deploy

- **dev:** automático no push para `main` (paths de `apps/**`), ou
  `workflow_dispatch`.
- **prod:** **só manual** (`workflow_dispatch` / tag `v*.*.*`) — gate por processo
  (GitHub Free não tem required reviewers). Remova o `if: false`/guard de
  `deploy-prod.yml` só quando a infra prod existir.

O deploy roda as **migrations** (job dedicado, antes da API) com paridade de
secrets (KEK inclusive — o backfill de cripto precisa do unwrap). Em seguida o
`smoke-alert` prova a entrega do alerta e o `Register runtime invariant` confere
(read-only) `--min-instances>=1` + `--no-cpu-throttling` (o heartbeat depende
disso; custo pendente de aprovação — ver CLAUDE.md §10).

### 5. Carteira de títulos — sincronização diária (Sprint 11, R5 / 11.6)

Não existe scheduler dentro da aplicação (a FASE 0 removeu Redis/ARQ; só há
`BackgroundTasks` e Cloud Run Jobs). A carteira sincroniza por **Cloud Run Job +
Cloud Scheduler**, no mesmo molde do job de limpeza — e **sem isso a carteira
envelhece e o aging mente**, sem nenhum sintoma na tela.

O `setup-gcp.sh <env>` do passo 1 já cria tudo, **1× por ambiente**:

| Recurso         | Nome                              | O que é                                                               |
| --------------- | --------------------------------- | --------------------------------------------------------------------- |
| Cloud Run Job   | `auditoria-api-sync-titles-<env>` | roda `python -m scripts.sync_client_titles` na imagem `auditoria-api` |
| Cloud Scheduler | `auditoria-sync-titles-<env>`     | dispara o job **1× por dia**, `04:12` `America/Sao_Paulo`             |
| SA de invocação | `auditoria-scheduler-<env>@…`     | só `run.invoker`, e **só nesse job**                                  |

Decisões que valem para os dois ambientes:

- **04:12** é deliberado: horário de baixa, e longe das bordas do cron de 25 min
  do cleanup (`:00`, `:25`, `:50`) — as duas cargas nunca disputam a janela de
  API da origem.
- **Sem retentativa automática** (`--max-retries=0` no job **e** no Scheduler):
  falha do ciclo é registrada e o ciclo **seguinte** tenta de novo. Retentar na
  hora criaria chamadas concorrentes do mesmo método para a mesma credencial —
  exatamente o que a origem não tolera.
- **Uma execução por ciclo** (`--tasks=1 --parallelism=1`) + teto de 30 min por
  execução: é o que impede o job de ficar preso em "sincronizando".
- A **configuração do job é clonada** do `cleanup`/`migrate` do ambiente, então
  os secrets (banco, cripto, alerta) são os mesmos **por construção** — nenhuma
  lista de secret mantida à mão neste script.

Conferência depois de rodar o script:

```bash
gcloud scheduler jobs describe auditoria-sync-titles-<env> \
  --location=southamerica-east1 --project=liberdade-assessoria
# execução manual de conferência (o mesmo que o cron faz)
gcloud run jobs execute auditoria-api-sync-titles-<env> \
  --region=southamerica-east1 --project=liberdade-assessoria --wait
```

> O botão de sincronizar manual da tela é outro caminho (rota da API, papel
> backend). Este agendamento é o que garante a carteira **diária**.

### 6. Aviso de leads da landing pública (86e3fr9ut)

A landing (`/`) grava cada contato do formulário na tabela `leads` e avisa o canal
da ADL no Slack por um **incoming webhook próprio**, montado na API como
`LEADS_SLACK_WEBHOOK_URL` (secret `leads-slack-webhook-url-<env>`). Não é canal de
plantão: vazio, o lead é gravado e o aviso é pulado (`lead_notification_skipped` no
log, sem a URL). O `setup-gcp.sh <env>` cria o container com uma versão VAZIA; ele
tem de rodar **antes** do primeiro deploy que referencia a secret, senão o
`--update-secrets` do workflow falha.

Alguém com acesso ao Slack cria o incoming webhook no canal da ADL e o operador
adiciona o valor:

```bash
printf '%s' 'https://hooks.slack.com/services/XXX' \
  | gcloud secrets versions add leads-slack-webhook-url-<env> --data-file=- --project=liberdade-assessoria
```

A revisão seguinte da API lê o `:latest`. Conferência: envie um lead de teste pela
landing e veja a mensagem no canal; a linha em `leads` fica com `notified_at`
preenchido.

### 7. Reembrulho das DEKs para a KEK de OUTRO projeto (86e3r2egr, épico 86e3r2egb)

Só existe para a mudança de projeto do GCP. Chave do Cloud KMS não se move entre
projetos e toda DEK de cliente está embrulhada pela KEK do projeto antigo: o banco
importado no projeto novo fica indecifrável até cada `clients.dek_wrapped` ser
reembrulhado pela KEK nova. O script `apps/api/scripts/rewrap_client_deks.py` troca
SÓ esse blob: nenhum campo cifrado muda, o `KEK_KEY_ID` continua `k1` (§3.14) e o modo
do wrapper continua Cloud KMS dos dois lados (misturar com o wrapper local é recusado).

> ⚠️ **Invariante:** roda SOMENTE sobre a cópia importada na instância NOVA, nunca
> sobre o banco antigo. É o que mantém o rollback trivial: o banco antigo segue
> decifrável pela KEK antiga até o desligamento.

Pré-requisitos (no projeto novo `<novo>`, a KEK nova já criada pelo `setup-gcp.sh`):

```bash
# 1) A SA de runtime do projeto NOVO pode DECIFRAR (só isso) com a KEK ANTIGA.
#    O binding é no RECURSO da chave, no projeto antigo.
gcloud kms keys add-iam-policy-binding omie-encryption-kek \
  --keyring=auditoria-<env> --location=southamerica-east1 --project=<antigo> \
  --member="serviceAccount:auditoria-api-sa@<novo>.iam.gserviceaccount.com" \
  --role=roles/cloudkms.cryptoKeyDecrypter

# 2) Job one-off clonado do migrate do projeto novo (mesma receita do setup-gcp.sh com
#    o da carteira: export, troca de nome, `jobs replace`), com comando e origem:
gcloud run jobs update auditoria-api-rewrap-deks-<env> \
  --region=southamerica-east1 --project=<novo> \
  --command=python --args=-m,scripts.rewrap_client_deks,--dry-run \
  --update-env-vars="KEK_KMS_KEY_NAME_SOURCE=projects/<antigo>/locations/southamerica-east1/keyRings/auditoria-<env>/cryptoKeys/omie-encryption-kek" \
  --tasks=1 --parallelism=1 --max-retries=0
```

O ALVO é o `KEK_KMS_KEY_NAME` do próprio job (a KEK do projeto novo, a mesma do
serviço); a ORIGEM vem só de `KEK_KMS_KEY_NAME_SOURCE`, que o script exige e recusa se
for igual à alvo (saída 2, nada lido nem gravado).

Ordem, sempre com `--wait` e lendo a última linha da execução:

1. **`--dry-run`**: abre cada blob com as duas chaves e prova o embrulho novo em
   memória, sem gravar. Esperado: `errors=0` e `pending` = número de clientes com DEK.
   É aqui que se confirma a classe de exceção do KMS para "ciphertext de outra chave"
   (hipótese `InvalidArgument`, `WRONG_KEY_EXCEPTIONS` no script): se for outra, o
   dry-run ABORTA e a tupla é corrigida no código antes de seguir.
2. **execução** (`--args=-m,scripts.rewrap_client_deks`): commit por cliente, UPDATE
   condicional no blob antigo. Interrompida, roda de novo: o que já foi trocado conta
   como `already`. Grava `deks_reembrulhadas` em `usage_events`.
3. **execução de novo**: `pending=0` e `rewrapped=0` (idempotência provada).
4. **`--verify`**: decifra um campo real de cada cliente com a KEK nova pelo caminho do
   serviço. **`verify_failed=0` é a condição do corte**; `no_field` é cliente sem campo
   cifrado no envelope (a DEK abriu, não há o que decifrar).
5. Depois do corte validado: **revogar** o binding do passo 1
   (`remove-iam-policy-binding`, mesmos argumentos) e rodar `--verify` de novo, que
   continua com zero falhas sem a chave antiga.

Saídas: `0` ok; `1` terminou com cliente em falha (ids no log `rewrap_client`); `2`
configuração recusada; `3` abortado (permissão negada, prova que não fecha, linha
alterada no meio, erro inesperado). Log e saída só com contagens, `client_id` e os dois
nomes de chave.

## Verificação rápida

- [ ] `setup-gcp.sh <env>` rodou sem erro fatal; `KEK_KMS_KEY_NAME` impresso.
- [ ] Pelo menos um canal de alerta com valor real (`gcloud secrets versions list alert-webhook-url-<env>`).
- [ ] Secret `leads-slack-webhook-url-<env>` existe (vazia ou com a URL do canal da ADL) antes do deploy.
- [ ] Admin de monitoração criado + `SMOKE_ADMIN_EMAIL/PASSWORD` no Environment.
- [ ] `API_URL_<env>` setada no Environment.
- [ ] Deploy verde, com `smoke-alert` provando `delivered=true`.
- [ ] `auditoria-api-sync-titles-<env>` existe e o passo
      `Re-resolve :dev digest in sync-titles Job` do deploy **não** emitiu
      `::warning::` (se emitiu, o script do passo 1 ainda não rodou neste
      ambiente e a carteira não está agendada).
- [ ] `auditoria-sync-titles-<env>` agendado às `04:12` `America/Sao_Paulo`.
- [ ] Só na mudança de projeto: `rewrap_client_deks --verify` com `verify_failed=0` na
      instância nova, e o binding na KEK antiga revogado depois do corte (passo 7).
