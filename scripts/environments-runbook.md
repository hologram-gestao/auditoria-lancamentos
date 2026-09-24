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

## Verificação rápida

- [ ] `setup-gcp.sh <env>` rodou sem erro fatal; `KEK_KMS_KEY_NAME` impresso.
- [ ] Pelo menos um canal de alerta com valor real (`gcloud secrets versions list alert-webhook-url-<env>`).
- [ ] Admin de monitoração criado + `SMOKE_ADMIN_EMAIL/PASSWORD` no Environment.
- [ ] `API_URL_<env>` setada no Environment.
- [ ] Deploy verde, com `smoke-alert` provando `delivered=true`.
- [ ] `auditoria-api-sync-titles-<env>` existe e o passo
      `Re-resolve :dev digest in sync-titles Job` do deploy **não** emitiu
      `::warning::` (se emitiu, o script do passo 1 ainda não rodou neste
      ambiente e a carteira não está agendada).
- [ ] `auditoria-sync-titles-<env>` agendado às `04:12` `America/Sao_Paulo`.
