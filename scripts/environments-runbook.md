# Runbook do operador — provisionamento por ambiente (Sprint 3 + Sprint 11)

> Rodável para **dev E prod** (isolados: nunca compartilham KEK, secret nem serviço).
> Todo passo é **idempotente** ou **1× por ambiente**. Nada aqui derruba um deploy
> em andamento — os grants são best-effort; o único **gate** é o smoke de entrega
> do alerta (03.7), que é intencional.

## Ordem (por ambiente `<env>` ∈ {dev, prod})

### 0. Projeto novo (86e3r2egh, épico 86e3r2egb)

Só quando o ambiente sobe num projeto do GCP que ainda não tem nada (a mudança para o
`hologramos-512320`, número `114342061812`). Num projeto que já roda, pule para o passo 1.
O pool do Workload Identity e os serviços e jobs do Cloud Run foram criados à mão no
projeto antigo; os scripts abaixo versionam isso. O `setup_wif.sh` e o replay não têm
default de projeto (todo comando nomeia o destino); o `grant_cicd_iam.sh` e o
`setup-gcp.sh` têm o `hologramos-512320` como default.

**O corte** (importar o banco, reembrulhar, trocar os secrets do GitHub, deploy, smoke,
despausar os agendamentos) e o desligamento do projeto antigo estão, como foram
executados em 22 a 26/10/2026, na seção "Migração de projeto: como foi executada", no
fim deste runbook. O que
o corte troca no GitHub: os secrets `GCP_WORKLOAD_IDENTITY_PROVIDER`,
`GCP_API_SERVICE_ACCOUNT` e `GCP_WEB_SERVICE_ACCOUNT` (repositório) e a Variable
`API_URL_DEV` (Environment `development`); `GCP_PROJECT_ID` também, por coerência, mas
nenhum workflow o lê. O projeto do deploy é o `PROJECT_ID` escrito nos workflows, que já
apontam para o `hologramos-512320`: com os secrets antigos o deploy falha na autenticação,
e voltar ao projeto antigo é reverter o commit, não um secret.

Ordem, com `<antigo>` = id do projeto de origem, `<novo>` = id do projeto novo e
`<número>` = o número dele:

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
   O WIF só funciona depois do item 3: a SA recém-criada não tem nem
   `resourcemanager.projects.get` (vem com `run.developer`). A ordem é WIF → IAM.

2. **Cloud Build e Artifact Registry** (operação, uma vez):
   `gcloud services enable cloudbuild.googleapis.com artifactregistry.googleapis.com --project=<novo>`
   e `gcloud artifacts repositories create auditoria --repository-format=docker --location=southamerica-east1 --project=<novo>`.
3. **IAM do CI/CD**: `bash scripts/deploy/grant_cicd_iam.sh` (defaults `hologramos-512320` /
   `114342061812`; outro projeto, `PROJECT_ID=<novo> PROJECT_NUMBER=<número>`). A SA do
   Cloud Build que recebe `artifactregistry.writer` é descoberta por
   `gcloud builds get-default-service-account`: no `hologramos-512320` é a padrão do
   Compute (`114342061812-compute@developer.gserviceaccount.com`), e a legada
   `<número>@cloudbuild.gserviceaccount.com` não existe (`CLOUDBUILD_SA=<e-mail>` pula a
   descoberta). As SAs da API e da web recebem também `cloudbuild.builds.editor` e
   `serviceusage.serviceUsageConsumer`, por paridade com o projeto antigo, e
   `iam.serviceAccountUser` NA SA do Cloud Build: o `gcloud builds submit` roda o build como
   ela, e a do Compute (ao contrário da legada) exige `actAs` de quem dispara. Sem esse
   binding o deploy do corte (23/10/2026) falhou nos builds com "caller does not have
   permission to act as service account".
4. **`setup-gcp.sh`** (passo 1 abaixo) com `PROJECT_ID=<novo>`: KEK, secrets de alerta e de
   leads. Num projeto vazio a parte da carteira **avisa e segue** (não há job de
   referência ainda); ela é reexecutada no item 7.
5. **Build das imagens no destino**:
   `gcloud builds submit --config=docker/cloudbuild/cloudbuild-api.yaml --substitutions=_TAG=dev --project=<novo> .`
   e o mesmo para a web com `_API_URL=<URL nova da API>` (a URL da API é build-arg da web:
   a imagem do antigo não serve).
6. **Replay dos serviços e jobs**, primeiro em dry-run (lê a origem, grava os manifestos
   fora do repositório, mostra o `diff -u` e a prova de cada um):

   ```bash
   bash scripts/deploy/replay_cloud_run_manifests.sh \
     --source-project <antigo> \
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

7. **`setup-gcp.sh` de novo**: agora os serviços e o job `cleanup` existem. A carteira ganha
   contrato e Cloud Scheduler, e a seção 5b do script faz o que o ensaio de 22/10/2026 fez
   à mão: `run.invoker` para `allUsers` nos dois serviços (a política de IAM não vai no
   manifesto do replay: sem isso, 403 em todo acesso; em `prod`, só com `PUBLIC_INVOKER=1`), `cloudsql.client` para a
   `auditoria-api-sa`, a SA `auditoria-worker-sa` do job de limpeza (com `cloudsql.client`,
   `run.invoker` e `secretmanager.secretAccessor`, e com `iam.serviceAccountUser` para a
   SA com que o deploy se autentica, o secret `GCP_API_SERVICE_ACCOUNT`, hoje a
   `auditoria-api-sa` (`CI_DEPLOY_SA` muda): o job `migrate` do workflow faz
   `gcloud run jobs update` no job de limpeza, que roda como a `worker-sa`; sem o binding o
   deploy do corte recebeu `actAs` negado) e o Cloud Scheduler
   `auditoria-cleanup-stuck-<env>-hourly` (`0 * * * *` em `Etc/UTC`). Agendamento que já
   existe não é tocado: o que está pausado continua pausado.

> ⚠️ **O `replace` exige a imagem já existente no Artifact Registry do destino** (item 5):
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
- **Acesso público, Cloud SQL e o job de limpeza** (seção 5b, 86e3r2ehn): `run.invoker`
  para `allUsers` na API e na web, `cloudsql.client` para a SA de runtime, a SA
  `auditoria-worker-sa` e o agendamento de hora em hora da limpeza (passo 0, item 7).

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
  | gcloud secrets versions add alert-webhook-url-<env> --data-file=- --project=hologramos-512320
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
  --location=southamerica-east1 --project=hologramos-512320
# execução manual de conferência (o mesmo que o cron faz)
gcloud run jobs execute auditoria-api-sync-titles-<env> \
  --region=southamerica-east1 --project=hologramos-512320 --wait
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
  | gcloud secrets versions add leads-slack-webhook-url-<env> --data-file=- --project=hologramos-512320
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
   A classe de exceção do Cloud KMS para "ciphertext de outra chave" é `InvalidArgument`
   (`WRONG_KEY_EXCEPTIONS` no script), confirmada contra o KMS real no dry-run do ensaio
   (22/10/2026, 14 clientes `pending`, nenhum aborto) e de novo no do corte. Se um dia
   ela mudar, o dry-run ABORTA e a tupla é corrigida no código antes de seguir.
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

## Migração de projeto: como foi executada (épico 86e3r2egb, 22 a 26/10/2026)

O Hologram OS saiu do `liberdade-assessoria` (projeto compartilhado com outras áreas) para
o `hologramos-512320` (número `114342061812`). Esta seção é o roteiro REAL, na ordem em
que rodou, com as armadilhas que só a execução mostrou. Para uma próxima mudança de
projeto, repita a ordem: o passo 0 monta o destino, o passo 7 reembrulha, e o resto está
aqui.

**Princípio que segurou o rollback:** o banco antigo nunca recebeu escrita depois do
export final e nunca foi reembrulhado. Até o deploy do corte, voltar era
`--ingress=all` na API antiga e despausar os agendamentos de lá, nada mais.

### Ensaio (22/10/2026), sobre uma cópia parada no tempo

O ambiente do ensaio É o ambiente final: no corte só o dado foi refeito.

1. **Montagem do destino pelo passo 0** (WIF, IAM do CI, `setup-gcp.sh`, instância
   `auditoria-pg` espelhando a antiga campo a campo: ENTERPRISE, `db-g1-small`, zonal,
   PD_SSD 10 GB, backup 03:00 × 7, PITR 7 dias; bancos `auditoria_dev` e `auditoria_prod`
   em UTF8 / `en_US.UTF8`; usuários com os mesmos nomes e senhas novas, digitadas fora do
   Claude Code).
2. **Build a partir de uma cópia LIMPA da `main`**, nunca da pasta de trabalho:
   `git worktree add --detach <dir> origin/main` e o `gcloud builds submit` de lá. O
   `.gcloudignore` não exclui os arquivos locais do hub de agentes, que subiriam para o
   bucket de fonte do Cloud Build. A web é REBUILDADA no destino (a URL da API é
   build-arg).
3. **Replay dos manifestos** em dry-run, depois `--apply` (passo 0, item 6). A API nasce
   `internal`. A política de IAM dos serviços (`allUsers` invoker) NÃO vai no manifesto:
   sem ela, 403 em todo acesso (a seção 5b do `setup-gcp.sh` concede).
4. **Agendamentos do destino PAUSADOS** logo depois de criados
   (`gcloud scheduler jobs pause`): o `setup-gcp.sh` cria o da carteira ATIVO e não há
   `--paused`. O da limpeza também foi pausado. Ficam assim até o `--verify` do
   reembrulho.
5. **Backup on-demand na instância antiga**, ponto de restauração independente do export.
6. **Export `--offload`** do `auditoria_dev` para um bucket no projeto NOVO
   (`gs://hologramos-512320-migracao-sql`, uniform access, public access prevention
   `enforced`), com `roles/storage.objectAdmin` no BUCKET para a SA da instância antiga
   (export) e da nova (import), nunca no projeto.
7. **Import com `--user=auditoria_dev_user`** (o usuário do Alembic e da API). O dump do
   Cloud SQL não traz `OWNER TO`, extensão nem `CREATE ROLE`: o dono das tabelas é quem
   importa, e ele tem de ser o mesmo usuário com que o Alembic e a API conectam.
8. **Conferência do banco por execução AVULSA de um job existente** (o `promote`) com
   `--args` sobrescrito para um `python -c` que abre transação `READ ONLY` e loga só a
   versão do Alembic, os nomes das tabelas, o dono e as contagens. Critério: Alembic igual
   ao head da `main`, mesmo número de tabelas, dono `auditoria_dev_user` em todas,
   contagem de cada tabela IGUAL à do dump. Sem psql de fora e sem abrir a instância.
9. **Flags de escrita no Omie DESLIGADAS na API nova** (`OMIE_POSTING_ENABLED`,
   `OMIE_PAYABLE_POSTING_ENABLED`, `OMIE_INSTALLMENT_POSTING_ENABLED` = `false`). O replay
   as copia ligadas, e um "Lançar no Omie" no ensaio escreveria na contabilidade do
   cliente com a intenção registrada só num banco que o corte descarta (§3.16, critério
   de rollback). O `deploy-dev.yml` só faz `--update-env-vars` e nunca as toca.
10. **Reembrulho** pelo passo 7: `--dry-run`, execução, execução de novo, `--verify`.
11. **Smoke**: API aberta (`--ingress=all`) só depois do `--verify` em zero; login, lista
    de clientes, conciliação existente sem `[indecifrável]`, reprocessamento de uma
    concluída, conciliação nova com parse de PDF, job da carteira à mão; log sem 5xx e
    sem `decrypt_failed`. Depois, a API de volta a `internal` até o corte.

Números do ensaio: dump de 1,72 MB, 40 tabelas, 28.837 linhas iguais às do dump, 14
clientes com DEK (de 16), reembrulho `pending=14` → `rewrapped=14` → `already=14`,
`--verify` 14 ok e 0 falhas; carteira 14/14 sem falha.

### Corte (23/10/2026, 21:06 a 24/10 00:05 -03, cerca de 3 h)

1. Time avisado. Os 2 agendamentos do Hologram OS no ANTIGO pausados (os das outras
   áreas intocados).
2. API ANTIGA `--ingress=internal` (reversível em um comando; a web antiga passa a
   falhar, que é o desejado). Conferido no log que nada estava em processamento.
3. Backup on-demand final na antiga; export final para `corte-2026-10-23/` no bucket.
4. `auditoria_dev` do ensaio apagado e recriado vazio na nova; import do dump final com
   `--user=auditoria_dev_user`; conferência do item 8 do ensaio.
5. Reembrulho completo pelo passo 7 (`--verify` em zero é condição para seguir).
6. `grant_cicd_iam.sh` e `setup-gcp.sh` no novo, a partir da cópia limpa do PR do corte.
7. **Flags de escrita no Omie RELIGADAS na API nova** e conferidas no `describe`: o deploy
   não as religa sozinho.
8. Secrets do GitHub (`GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_API_SERVICE_ACCOUNT`,
   `GCP_WEB_SERVICE_ACCOUNT`, `GCP_PROJECT_ID`) e a Variable `API_URL_DEV` trocados. Os
   valores antigos (identificadores, não credenciais) anotados para o rollback.
9. PR do CI/CD mergeado (develop → main). O push da `main` só rodou `changes` e
   `summary`: o filtro de paths do `deploy-dev.yml` não dispara build por mudança em
   workflow.
10. **API nova aberta ANTES do `workflow_dispatch`**: o `smoke-alert` do Deploy to Dev
    chama a API pela URL pública (`vars.API_URL_DEV`).
11. `Deploy to Dev` por `workflow_dispatch` com `target=all`. Foram **3 tentativas**:
    - 1ª: os dois builds falharam com "caller does not have permission to act as service
      account": no projeto novo o build roda como a SA do Compute, que exige
      `iam.serviceAccountUser` das SAs do CI. Os builds do ensaio passaram porque rodaram
      como owner.
    - 2ª: builds ok; o job `migrate` falhou ao atualizar o job de limpeza (`actAs` negado
      na `auditoria-worker-sa` para a SA do deploy). O Alembic não chegou a rodar.
    - 3ª: verde, com o smoke `delivered=true` e a invariante `minScale 1` /
      `cpu-throttling false` sem aviso.

    Os dois `actAs` só aparecem no deploy PELO CI e hoje estão nos scripts (passo 0, itens
    3 e 7).

12. Conferência pós-deploy: revisões novas, flags `true`, KEK e `FRONTEND_URL` novos, log
    sem erro, banco com as contagens do dump (mais os eventos do smoke).
13. Agendamentos do novo despausados.
14. Binding da SA nova na KEK antiga REVOGADO e `--verify` de novo, sem ela: 14 ok, 0
    falhas.
15. `alembic check` no novo (execução avulsa do job `migrate` com `--args=check`, rodada
    em 24/10 na conferência das subtasks): "No new upgrade operations detected", sem
    drift. É critério de fechamento do banco, não detalhe.

Números do corte: dump de 1,73 MB, 40 tabelas, 28.948 linhas, Alembic `c8d1f4e6a2b9`,
14 DEKs reembrulhadas, `--verify` 14/0 antes e depois da revogação, 3 tentativas de
deploy. No primeiro dia útil (26/10), uso real sem 5xx, sem `decrypt_failed` e sem alerta.

### Desligamento do antigo (26/10/2026)

Só depois de todas as subtasks do corte conferidas contra o critério, e de provar que o
banco antigo não recebeu escrita desde o corte (zero execuções de job e zero requisições
na API desde 24/10 00:18 -03): com isso, o dump do corte é o último estado. Inventário
primeiro, separando o que é do Hologram OS do que é das outras áreas do projeto (que não
se toca), e então, nesta ordem:

1. agendamentos;
2. jobs;
3. serviços;
4. instância do Cloud SQL, com backup FINAL (`eaa404dd-7efa-44e2-ae9a-cf977c569c6a`, válido
   até 24/01/2027);
5. secrets (pré-checagem: nenhum serviço ou job restante os monta);
6. repositório do Artifact Registry;
7. pool do WIF (recuperável por 30 dias);
8. papéis de projeto e SAs (recuperáveis por 30 dias).

No projeto novo, depois: o job one-off do reembrulho e o `objectAdmin` das SAs do Cloud
SQL no bucket.

**Fica de propósito:** o keyring `auditoria-dev` com a KEK antiga no `liberdade-assessoria`.
O `dek_wrapped` do dump do corte está embrulhado por ela; destruí-la torna o dump
inútil. Retenção decidida pelo Pedro: o dump do ensaio apagado; o do corte
(`gs://hologramos-512320-migracao-sql/corte-2026-10-23/`) guardado até 24/01/2027 por
regra de ciclo de vida do bucket (`Delete`, `age` 92), a mesma validade do backup final.
**Em 24/01/2027:** apagar o bucket vazio e desativar a KEK antiga.

### Armadilhas, em uma linha cada

- O `.gcloudignore` não exclui arquivos locais do hub: build sempre de um `git worktree`
  limpo da `main`.
- O dump do Cloud SQL não tem dono: import com `--user=<usuário do app>`.
- O replay não leva IAM: o `allUsers` invoker dos serviços, o `cloudsql.client` da
  `auditoria-api-sa` e a `auditoria-worker-sa` com os papéis dela vêm do `setup-gcp.sh` 5b.
- Prova verde não é manifesto aplicável: a prova do replay passou a fazer parse do YAML
  depois que um `*.run.app` sem aspas virou alias.
- As flags de escrita no Omie vão copiadas ligadas pelo replay e o deploy não as toca:
  desligar no ensaio, religar no corte.
- O agendamento nasce ativo: pausar até o `--verify`.
- O smoke do deploy usa a URL pública: abrir a API antes do `workflow_dispatch`.
- Dois `actAs` só o deploy pelo CI revela (SA do build e `worker-sa`).
- `alembic check` sem drift fecha o corte.
- A KEK antiga só morre depois do último dump que ela abre.

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
- [ ] Só na mudança de projeto: `setup_wif.sh` imprimiu o provider, o `grant_cicd_iam.sh`
      rodou depois dele e o `replay_cloud_run_manifests.sh` deu prova OK nos 7 recursos
      antes do `--apply` (passo 0).
- [ ] Só na mudança de projeto: `rewrap_client_deks --verify` com `verify_failed=0` na
      instância nova, e o binding na KEK antiga revogado depois do corte (passo 7).
