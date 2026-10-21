# Runbook do operador — provisionamento por ambiente (Sprint 3 + Sprint 11)

> Rodável para **dev E prod** (isolados: nunca compartilham KEK, secret nem serviço).
> Todo passo é **idempotente** ou **1× por ambiente**. Nada aqui derruba um deploy
> em andamento — os grants são best-effort; o único **gate** é o smoke de entrega
> do alerta (03.7), que é intencional.

## Ordem (por ambiente `<env>` ∈ {dev, prod})

### 0. Projeto novo (86e3r2egh, épico 86e3r2egb)

Só quando o ambiente sobe num projeto do GCP que ainda não tem nada (a mudança do
`liberdade-assessoria` para o `hologramos-512320`). Num projeto que já roda, pule para o passo 1.
O pool do Workload Identity e os serviços e jobs do Cloud Run foram criados à mão no
projeto antigo; os dois scripts abaixo versionam isso. Nenhum dos dois tem default de
projeto: todo comando nomeia o destino.

Ordem, com `<novo>` = id do projeto novo e `<número>` = o número dele:

1. **WIF e SAs** (idempotente, guard por `describe` em cada passo):

   ```bash
   PROJECT_ID=<novo> PROJECT_NUMBER=<número> \
   API_SA_DISPLAY_NAME='<o do antigo>' WEB_SA_DISPLAY_NAME='<o do antigo>' \
     bash scripts/deploy/setup_wif.sh
   ```

   Cria `auditoria-api-sa`, `auditoria-web-sa`, o pool `auditoria-github-pool`, o provider
   `auditoria-github-provider` (issuer, mapeamento e condição do repositório iguais aos do
   antigo) e o `workloadIdentityUser` das duas SAs. Nunca cria chave de SA. Os display
   names saem de `gcloud iam service-accounts list --project=<antigo>`; sem eles, a SA
   nasce com o próprio id como nome. No fim imprime os três valores dos secrets do GitHub.

2. **Cloud Build e Artifact Registry** (operação, uma vez):
   `gcloud services enable cloudbuild.googleapis.com artifactregistry.googleapis.com --project=<novo>`
   e `gcloud artifacts repositories create auditoria --repository-format=docker --location=southamerica-east1 --project=<novo>`.
   Confira qual SA o Cloud Build do projeto novo usa
   (`gcloud builds get-default-service-account --project=<novo>`): projeto novo pode não
   ter mais a legada `<número>@cloudbuild.gserviceaccount.com`, que é a que o item 3
   autoriza no Artifact Registry. Se for outra, o binding de `artifactregistry.writer` vai
   para ela.
3. **IAM do CI/CD**: `PROJECT_ID=<novo> PROJECT_NUMBER=<número> bash scripts/deploy/grant_cicd_iam.sh`
   (os defaults dele ainda são do projeto antigo: as duas variáveis são obrigatórias aqui).
4. **Fumaça do WIF**: grave os valores impressos no item 1 como
   `GCP_WORKLOAD_IDENTITY_PROVIDER_NEW` e `GCP_API_SERVICE_ACCOUNT_NEW` no Environment
   `development` e dispare o `wif-smoke.yml` (Actions → WIF smoke → Run workflow). Ele só
   autentica e lista; os secrets do deploy atual não mudam. Workflow só de
   `workflow_dispatch` só aparece em Actions depois que o arquivo está na `main`
   (PR develop → main).
5. **`setup-gcp.sh`** (passo 1 abaixo) com `PROJECT_ID=<novo>`: KEK, secrets de alerta e de
   leads. Num projeto vazio a parte da carteira **avisa e segue** (não há job de
   referência ainda); ela é reexecutada no item 8.
6. **Build das imagens no destino**:
   `gcloud builds submit --config=docker/cloudbuild/cloudbuild-api.yaml --substitutions=_TAG=dev --project=<novo> .`
   e o mesmo para a web com `_API_URL=<URL nova da API>` (a URL da API é build-arg da web:
   a imagem do antigo não serve).
7. **Replay dos serviços e jobs**, primeiro em dry-run (lê a origem, grava os manifestos
   fora do repositório, mostra o `diff -u` e a prova de cada um):

   ```bash
   bash scripts/deploy/replay_cloud_run_manifests.sh \
     --source-project liberdade-assessoria \
     --target-project <novo> --target-project-number <número> \
     --target-sql-connection <novo>:southamerica-east1:<instância> \
     --target-kek projects/<novo>/locations/southamerica-east1/keyRings/auditoria-dev/cryptoKeys/omie-encryption-kek \
     --api-url https://auditoria-api-dev-<número>.southamerica-east1.run.app \
     --web-url https://auditoria-web-dev-<número>.southamerica-east1.run.app \
     --out-dir /tmp/replay-<novo>
   ```

   Prova verde nos 7 recursos → o mesmo comando com `--apply` e o MESMO `--out-dir`
   (`services replace` / `jobs replace`, um por vez, para no primeiro erro). O `--apply`
   lê a origem de novo e só aplica se o manifesto gerado for idêntico ao revisado no
   dry-run: um deploy no antigo entre os dois faz ele parar e mostrar o diff. Prova
   vermelha = nada é aplicado: o manifesto tem uma referência ao projeto antigo que a
   reescrita não conhece, e o trecho aparece na saída.

   Use a URL da API na forma com o NÚMERO (`…-<número>.southamerica-east1.run.app`) em
   todo lugar (Variable `API_URL_DEV`, `_API_URL` do build da web). A forma com hash
   (`…-<hash>-rj.a.run.app`, a que o `status.url` mostra) só existe depois do primeiro
   `replace` e não entra no `ALLOWED_HOSTS`: requisição com esse host recebe 400 até
   alguém acrescentá-lo.

8. **`setup-gcp.sh` de novo**: agora o job `cleanup` existe e a carteira ganha contrato e
   Cloud Scheduler.

> ⚠️ **O `replace` exige a imagem já existente no Artifact Registry do destino** (item 6):
> o manifesto aponta para `<AR>/<novo>/auditoria/<imagem>:dev`, e um digest do antigo
> vira essa tag porque o build no destino gera outro digest. Além da imagem, a revisão da
> API só fica pronta com os secrets montados existindo no destino (subtask dos secrets) e
> a instância do Cloud SQL de pé: sem eles o `replace` da API falha e o script para ali.
>
> ⚠️ **A API nova nasce `internal`** (`--api-ingress`, default `internal`; a prova
> confere): até o `--verify` do reembrulho zerar (passo 7), ela não recebe tráfego
> público, e os schedulers do projeto novo ficam pausados. Abrir é
> `gcloud run services update auditoria-api-dev --ingress=all` depois do `--verify`. A
> web mantém o ingresso da origem.

O dry-run tem teste sem gcloud, sobre a fixture inventada de `scripts/deploy/testdata/`:
`bash scripts/deploy/test_replay_cloud_run_manifests.sh` (saída 0 = passou).

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

> ⚠️ **Sem tráfego e sem job agendado no projeto novo enquanto o reembrulho não termina.**
> Entre a importação do banco e o `--verify` final, todo cliente ainda não reembrulhado é
> indecifrável para o serviço novo: a API do projeto novo fica sem ingresso público
> (`--ingress=internal`) e os Cloud Schedulers de lá pausados
> (`gcloud scheduler jobs pause`) até o passo 4 dar zero falhas. Sem isso, cada request e
> cada ciclo da carteira vira 500, `[indecifrável]` e alerta de plantão que não é defeito.

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
   cifrado no envelope (a DEK abriu, não há o que decifrar). Ele NÃO cobre linha bare
   legada (cifrada com a `OMIE_ENCRYPTION_KEY`, sem DEK): essa depende de o secret da
   `OMIE_ENCRYPTION_KEY` do projeto novo ser cópia exata do antigo (landmine 2), conferido
   à parte na subtask dos secrets.
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
- [ ] Só na mudança de projeto: `setup_wif.sh` imprimiu o provider, o `wif-smoke.yml`
      ficou verde e o `replay_cloud_run_manifests.sh` deu prova OK nos 7 recursos antes do
      `--apply` (passo 0).
- [ ] Só na mudança de projeto: `rewrap_client_deks --verify` com `verify_failed=0` na
      instância nova, e o binding na KEK antiga revogado depois do corte (passo 7).
