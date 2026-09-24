#!/bin/bash
# Provisiona a infra de 1ª vez POR AMBIENTE que o deploy assume já existir.
# Rode UMA vez por ambiente (dev e prod são isolados) ANTES do primeiro deploy
# que a consome. Provisiona (Sprint 3):
#   - Req. 1 / INFRA 03.1: KEK no Cloud KMS (envelope encryption). O backend
#     (03.3/03.4) faz wrap/unwrap das DEKs por cliente contra ela; nunca sai do KMS.
#   - Req. 4 / INFRA 03.7: secrets do canal de alerta (ALERT_WEBHOOK_URL/
#     ALERT_EMAIL_TO). A PROVA de entrega (smoke pós-deploy) dispara o gatilho
#     SINTÉTICO da 03.6 — POST /api/v1/system/alert-test no serviço já deployado —
#     como admin de monitoração; ver instruções de bootstrap no fim.
# E (Sprint 11):
#   - R5 / INFRA 11.6: Cloud Run Job + Cloud Scheduler da sincronização DIÁRIA da
#     carteira de títulos (`python -m scripts.sync_client_titles`). Não há
#     scheduler dentro da aplicação — o molde é o job de limpeza que já roda.
#
# ⚠️ ORDEM IMPORTA: rode este script ANTES do próximo deploy — o `--update-secrets
#    ...:latest` do workflow FALHA se o secret não tiver versão (semeamos vazia).
#    Depois adicione o VALOR real do canal (≥1) — instruções impressas no fim.
#
# Idempotente — pode rodar N vezes:
#   - keyring/CryptoKey do KMS NÃO podem ser deletados; criamos só se não existirem
#     (guard por `describe`), então re-rodar não falha nem duplica.
#   - secrets/Job: guard por `describe`; `add-iam-policy-binding` é no-op se já existe.
#
# Menor privilégio: a SA de RUNTIME recebe cloudkms.cryptoKeyEncrypterDecrypter e
# secretmanager.secretAccessor SOMENTE nos recursos (per-key / per-secret), nunca
# no projeto. A SA de deploy NÃO recebe acesso de runtime aqui.
#
# Pré-requisitos:
#   - gcloud autenticado com cloudkms.admin + secretmanager.admin no projeto.
#   - A SA de runtime (auditoria-api-sa) já criada (grant_cicd_iam.sh).
#   - APIs cloudkms/secretmanager habilitadas (o script tenta; best-effort).
#
# Uso:
#   bash scripts/setup-gcp.sh <dev|prod>

set -euo pipefail

# ------------------------------------------------------------------
# Args / config
# ------------------------------------------------------------------
ENV="${1:-}"
if [[ "$ENV" != "dev" && "$ENV" != "prod" ]]; then
  echo "::error:: uso: bash scripts/setup-gcp.sh <dev|prod>" >&2
  exit 2
fi

PROJECT_ID="${PROJECT_ID:-liberdade-assessoria}"
REGION="${REGION:-southamerica-east1}"
AR_HOST="${AR_HOST:-southamerica-east1-docker.pkg.dev}"
AR_REPO="${AR_REPO:-auditoria}"

# Keyring sufixado por ambiente → dev e prod NUNCA compartilham KEK.
KEYRING="${KEYRING:-auditoria-${ENV}}"
# A KEK (Key Encryption Key) que embrulha as DEKs por cliente. Nome estável por
# ambiente; a rotação da KEK é decisão operacional (fora do escopo da Sprint 3).
KEK_KEY="${KEK_KEY:-omie-encryption-kek}"

# SA de RUNTIME que o serviço da API e o job de migração usam (mesma SA nos dois
# — a paridade serviço×job depende disso: o backfill 03.4 roda no job e precisa
# do unwrap). Prod pode, no futuro, ter uma SA dedicada — sobrescreva RUNTIME_SA.
RUNTIME_SA="${RUNTIME_SA:-auditoria-api-sa@${PROJECT_ID}.iam.gserviceaccount.com}"

KEK_KMS_KEY_NAME="projects/${PROJECT_ID}/locations/${REGION}/keyRings/${KEYRING}/cryptoKeys/${KEK_KEY}"

# --- Jobs Cloud Run (Sprint 11, R5) — TUDO sufixado por ambiente ------------
# dev e prod nunca compartilham job, agendamento nem SA de invocação.
MIGRATE_JOB="${MIGRATE_JOB:-auditoria-api-migrate-${ENV}}"
CLEANUP_JOB="${CLEANUP_JOB:-auditoria-api-cleanup-stuck-${ENV}}"
SYNC_TITLES_JOB="${SYNC_TITLES_JOB:-auditoria-api-sync-titles-${ENV}}"
# Cron DIÁRIO em horário de baixa. Minuto 12 é deliberado: o cleanup roda de
# 25 em 25 minutos (:00, :25, :50), então :12 fica longe de todas as bordas e
# as duas cargas nunca disputam a mesma janela de API da origem.
SYNC_TITLES_SCHEDULE="${SYNC_TITLES_SCHEDULE:-12 4 * * *}"
SYNC_TITLES_TZ="${SYNC_TITLES_TZ:-America/Sao_Paulo}"
# Teto por execução. A ingestão é SERIAL por cliente (limite de requisições da
# origem), então precisa de folga sobre os 10min de default do Cloud Run Jobs —
# e um teto é o que impede a execução de ficar presa em "sincronizando".
SYNC_TITLES_TASK_TIMEOUT="${SYNC_TITLES_TASK_TIMEOUT:-1800s}"
# Cloud Scheduler + a SA que ele usa para INVOCAR o job (menor privilégio: só
# run.invoker, e só NESTE job — não é a SA de runtime nem a de deploy).
SYNC_TITLES_SCHEDULER="${SYNC_TITLES_SCHEDULER:-auditoria-sync-titles-${ENV}}"
SCHEDULER_SA_ID="${SCHEDULER_SA_ID:-auditoria-scheduler-${ENV}}"
SCHEDULER_SA="${SCHEDULER_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "=== setup-gcp (${ENV}) — projeto ${PROJECT_ID} / região ${REGION} ==="
echo "    keyring:     ${KEYRING}"
echo "    KEK:         ${KEK_KEY}"
echo "    runtime SA:  ${RUNTIME_SA}"
echo ""

# ------------------------------------------------------------------
# 0. Habilitar APIs necessárias (best-effort — não derruba se já habilitadas)
# ------------------------------------------------------------------
echo "--- Habilitando cloudkms + secretmanager + run + scheduler + iam (best-effort) ---"
gcloud services enable \
  cloudkms.googleapis.com \
  secretmanager.googleapis.com \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  iam.googleapis.com \
  --project="$PROJECT_ID" --quiet \
  || echo "::warning:: não foi possível habilitar as APIs (talvez já estejam / sem permissão)"

# ------------------------------------------------------------------
# 1. Keyring (idempotente via describe-guard — keyring não é deletável)
# ------------------------------------------------------------------
echo ""
echo "--- Keyring ${KEYRING} ---"
if gcloud kms keyrings describe "$KEYRING" \
     --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "  já existe — ok"
else
  echo "  criando…"
  gcloud kms keyrings create "$KEYRING" \
    --location="$REGION" --project="$PROJECT_ID" --quiet
fi

# ------------------------------------------------------------------
# 2. CryptoKey simétrica ENCRYPT_DECRYPT (idempotente via describe-guard)
# ------------------------------------------------------------------
echo ""
echo "--- CryptoKey ${KEK_KEY} (purpose=encryption / symmetric) ---"
if gcloud kms keys describe "$KEK_KEY" \
     --keyring="$KEYRING" --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "  já existe — ok"
else
  echo "  criando…"
  # --purpose=encryption → chave simétrica ENCRYPT_DECRYPT (default).
  # Sem rotação automática: a KEK é estável; rotacioná-la é decisão operacional
  # (Req. 1 declara isso fora de escopo). A rotação das DEKs é feita no app.
  gcloud kms keys create "$KEK_KEY" \
    --keyring="$KEYRING" --location="$REGION" --project="$PROJECT_ID" \
    --purpose=encryption --quiet
fi

# ------------------------------------------------------------------
# 3. IAM per-key — MENOR PRIVILÉGIO
#    A SA de runtime só pode encrypt/decrypt COM esta chave; nunca exporta a KEK
#    (não existe role/permissão que "baixe" o material — só wrap/unwrap no KMS).
#    Binding no RECURSO da chave, jamais no projeto.
# ------------------------------------------------------------------
echo ""
echo "--- IAM: ${RUNTIME_SA} → cloudkms.cryptoKeyEncrypterDecrypter (per-key) ---"
if gcloud iam service-accounts describe "$RUNTIME_SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud kms keys add-iam-policy-binding "$KEK_KEY" \
    --keyring="$KEYRING" --location="$REGION" --project="$PROJECT_ID" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/cloudkms.cryptoKeyEncrypterDecrypter" \
    --quiet >/dev/null \
    && echo "  concedido (ou já existia)" \
    || echo "::warning:: falha ao conceder o binding — verifique permissão de cloudkms.admin"
else
  echo "::warning:: SA ${RUNTIME_SA} não existe ainda — rode grant_cicd_iam.sh / crie a SA e re-execute."
fi

# ------------------------------------------------------------------
# 4. Canais de alerta (Sprint 3, Req. 4 / INFRA 03.7) — Secret Manager
#    ALERT_WEBHOOK_URL / ALERT_EMAIL_TO apontam para um endereço COMPARTILHADO
#    da equipe de plantão (nunca uma pessoa). O VALOR real (URL/e-mail do canal)
#    é credencial de operação — NÃO versionada no repo; o operador adiciona a
#    versão. Aqui criamos só o CONTAINER + secretAccessor per-secret e semeamos
#    uma versão VAZIA (pra o `--update-secrets ...:latest` do deploy resolver).
#    Fail-closed: se AMBOS ficarem vazios, o app RECUSA subir (guarda no backend).
# ------------------------------------------------------------------
ALERT_WEBHOOK_SECRET="alert-webhook-url-${ENV}"
ALERT_EMAIL_SECRET="alert-email-to-${ENV}"

ensure_alert_secret() {
  local name="$1"
  echo "--- Secret ${name} ---"
  if gcloud secrets describe "$name" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "  container já existe — ok"
  else
    echo "  criando container…"
    gcloud secrets create "$name" \
      --replication-policy=automatic --project="$PROJECT_ID" --quiet
  fi
  # Semeia UMA versão vazia se não houver nenhuma — assim `:latest` resolve no
  # deploy mesmo antes de o operador colocar o valor real. Vazio ⇒ "canal não
  # configurado" (o fail-closed do backend cobre o caso de AMBOS vazios).
  if [ -z "$(gcloud secrets versions list "$name" \
              --project="$PROJECT_ID" --format='value(name)' --limit=1 2>/dev/null)" ]; then
    printf '' | gcloud secrets versions add "$name" --data-file=- \
      --project="$PROJECT_ID" --quiet >/dev/null
    echo "  versão vazia semeada (operador deve adicionar o valor REAL do canal)"
  fi
  # secretAccessor PER-SECRET à SA de runtime (menor privilégio).
  if gcloud iam service-accounts describe "$RUNTIME_SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud secrets add-iam-policy-binding "$name" \
      --member="serviceAccount:${RUNTIME_SA}" \
      --role="roles/secretmanager.secretAccessor" \
      --project="$PROJECT_ID" --quiet >/dev/null \
      && echo "  secretAccessor concedido (ou já existia)" \
      || echo "::warning:: falha ao conceder secretAccessor em ${name}"
  else
    echo "::warning:: SA ${RUNTIME_SA} não existe — conceda secretAccessor em ${name} depois."
  fi
}

echo ""
echo "=== Canais de alerta (${ENV}) ==="
ensure_alert_secret "$ALERT_WEBHOOK_SECRET"
ensure_alert_secret "$ALERT_EMAIL_SECRET"

# ------------------------------------------------------------------
# 5. Sincronização diária da CARTEIRA DE TÍTULOS (Sprint 11, R5 / INFRA 11.6)
#    Não existe scheduler DENTRO da aplicação (a FASE 0 removeu Redis/ARQ; só
#    há BackgroundTasks e Cloud Run Jobs). O molde é o job de limpeza que já
#    roda: Cloud Run Job + Cloud Scheduler.
#
#    ⚠️ POR QUE CLONAR O JOB DE REFERÊNCIA, e não declarar os secrets aqui:
#    o conjunto completo de secrets do job (DATABASE_URL, JWT, chaves de cripto,
#    Anthropic…) NÃO está versionado — os jobs de dev foram criados à mão. Um
#    `--set-secrets` escrito de memória produziria um job que sobe sem
#    DATABASE_URL e falha todo dia às 4h sem ninguém olhar. Então a config vem
#    do job que JÁ roda no ambiente (cleanup, senão migrate): paridade
#    serviço × job por CONSTRUÇÃO, não por lista mantida à mão. Em cima disso o
#    script reforça, explicitamente, o que é contrato desta task: comando,
#    argumentos, SA de runtime, KEK, secrets de alerta e política de retentativa.
#
#    Idempotente: `jobs replace` cria ou substitui; `jobs update`, `add-iam-
#    policy-binding` e o par describe/create do Scheduler são no-op se já está
#    no estado desejado. Rodar 2× não falha nem duplica.
#
#    Nenhum passo aqui derruba o provisionamento do resto: tudo é best-effort
#    com `|| warning`.
# ------------------------------------------------------------------
echo ""
echo "=== Carteira de títulos — Job diário (${ENV}) ==="

# Job de referência: de onde a configuração (secrets, Cloud SQL, rede, env) é
# clonada. Preferimos o cleanup (também é um job de cron, mesma forma).
REF_JOB=""
for _candidate in "$CLEANUP_JOB" "$MIGRATE_JOB"; do
  if gcloud run jobs describe "$_candidate" \
       --region="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    REF_JOB="$_candidate"
    break
  fi
done

if [ -z "$REF_JOB" ]; then
  echo "::warning:: nenhum job de referência (${CLEANUP_JOB} / ${MIGRATE_JOB}) existe em ${ENV}."
  echo "            A carteira NÃO será agendada. Crie os jobs do ambiente (deploy) e re-execute este script."
else
  echo "  job de referência: ${REF_JOB}"

  # ---- 5.1 Cloud Run Job ------------------------------------------------
  if gcloud run jobs describe "$SYNC_TITLES_JOB" \
       --region="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "--- Job ${SYNC_TITLES_JOB} já existe — só reaplicando o contrato ---"
  else
    echo "--- Criando ${SYNC_TITLES_JOB} a partir de ${REF_JOB} ---"
    _tmp="$(mktemp -d)"
    trap 'rm -rf "${_tmp}"' EXIT
    if gcloud run jobs describe "$REF_JOB" \
         --region="$REGION" --project="$PROJECT_ID" --format=export > "${_tmp}/ref.yaml" 2>/dev/null; then
      # Troca o nome do job em TODAS as ocorrências (metadata + template) e
      # descarta as annotations que são carimbo da última operação no job de
      # origem — recriá-las em outro recurso confunde o histórico.
      sed "s/${REF_JOB}/${SYNC_TITLES_JOB}/g" "${_tmp}/ref.yaml" \
        | grep -v -E 'run\.googleapis\.com/(operation-id|lastModifier|creator)|client\.knative\.dev/nonce' \
        > "${_tmp}/sync.yaml" || true
      if [ -s "${_tmp}/sync.yaml" ]; then
        gcloud run jobs replace "${_tmp}/sync.yaml" \
          --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null \
          && echo "  criado (config clonada de ${REF_JOB})" \
          || echo "::warning:: falha ao criar ${SYNC_TITLES_JOB} a partir de ${REF_JOB}"
      else
        echo "::warning:: export de ${REF_JOB} saiu vazio — ${SYNC_TITLES_JOB} não foi criado."
      fi
    else
      echo "::warning:: não foi possível exportar a config de ${REF_JOB} — ${SYNC_TITLES_JOB} não foi criado."
    fi
  fi

  # ---- 5.2 Contrato explícito do job ------------------------------------
  # `--update-*` faz MERGE (não zera o que veio do clone). O comando/args são o
  # contrato com a BACK 11.2: o mesmo módulo runnable, na imagem auditoria-api.
  # --max-retries=0 + --parallelism=1 + --tasks=1: uma execução por ciclo, sem
  # empilhar. Falha é registrada e o ciclo SEGUINTE tenta de novo (é o que o R5
  # pede) — retentativa automática aqui só criaria chamadas concorrentes do
  # mesmo método para a mesma credencial, que é exatamente o que a origem não
  # tolera.
  #
  # Dos secrets, remontamos aqui só os DOIS que este script garante existir com
  # versão (§4). O canal SINTÉTICO não é criado aqui — montá-lo antes de existir
  # faria o `update` inteiro falhar e o job ficaria SEM comando; ele vem no
  # clone e é remontado (com a KEK) a cada deploy, pelo workflow.
  if gcloud run jobs describe "$SYNC_TITLES_JOB" \
       --region="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "--- Reaplicando contrato em ${SYNC_TITLES_JOB} ---"
    gcloud run jobs update "$SYNC_TITLES_JOB" \
      --region="$REGION" --project="$PROJECT_ID" \
      --command=python \
      --args=-m,scripts.sync_client_titles \
      --service-account="$RUNTIME_SA" \
      --update-env-vars="KEK_KMS_KEY_NAME=${KEK_KMS_KEY_NAME}" \
      --update-secrets="ALERT_WEBHOOK_URL=${ALERT_WEBHOOK_SECRET}:latest,ALERT_EMAIL_TO=${ALERT_EMAIL_SECRET}:latest" \
      --tasks=1 \
      --parallelism=1 \
      --max-retries=0 \
      --task-timeout="$SYNC_TITLES_TASK_TIMEOUT" \
      --quiet >/dev/null \
      && echo "  ok (python -m scripts.sync_client_titles, SA ${RUNTIME_SA})" \
      || echo "::warning:: falha ao aplicar o contrato em ${SYNC_TITLES_JOB}"

    # SA de runtime: nunca a default do compute, nunca a de deploy. Conferência
    # explícita — o erro é silencioso (o job só falha no 1º acesso a secret).
    _job_sa="$(gcloud run jobs describe "$SYNC_TITLES_JOB" \
                 --region="$REGION" --project="$PROJECT_ID" \
                 --format='value(spec.template.spec.template.spec.serviceAccountName)' 2>/dev/null || echo "")"
    case "$_job_sa" in
      "") echo "::warning:: não foi possível ler a SA de ${SYNC_TITLES_JOB}." ;;
      *-compute@developer.gserviceaccount.com)
        echo "::warning:: ${SYNC_TITLES_JOB} está com a SA DEFAULT do compute (${_job_sa}) — ela não tem secretAccessor." ;;
      *) echo "  SA de runtime: ${_job_sa}" ;;
    esac

    # ---- 5.3 secretAccessor POR SECRET ----------------------------------
    # `--set-secrets` MONTA mas NÃO concede. Como a SA é a mesma do job de
    # referência os grants já devem existir; este laço é reforço idempotente e
    # cobre o secret que só este job passe a montar.
    echo "--- secretAccessor por secret montado em ${SYNC_TITLES_JOB} ---"
    _mounted="$(gcloud run jobs describe "$SYNC_TITLES_JOB" \
                  --region="$REGION" --project="$PROJECT_ID" --format=export 2>/dev/null \
                  | grep -A3 'secretKeyRef:' | grep -E '^[[:space:]]*name:' \
                  | awk '{print $2}' | tr -d "'\"" | sort -u || true)"
    if [ -z "$_mounted" ]; then
      echo "  nenhum secret montado (ou não foi possível ler) — nada a conceder"
    else
      echo "$_mounted" | while read -r _secret; do
        [ -n "$_secret" ] || continue
        gcloud secrets add-iam-policy-binding "$_secret" \
          --member="serviceAccount:${RUNTIME_SA}" \
          --role="roles/secretmanager.secretAccessor" \
          --project="$PROJECT_ID" --quiet >/dev/null 2>&1 \
          && echo "  ${_secret}: ok" \
          || echo "::warning:: falha ao conceder secretAccessor em ${_secret}"
      done
    fi

    # ---- 5.4 cloudsql.client --------------------------------------------
    # O job fala com o Cloud SQL pelo mesmo socket do serviço. A role só existe
    # no nível do projeto; binding no-op se já existir.
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:${RUNTIME_SA}" \
      --role="roles/cloudsql.client" \
      --condition=None --quiet >/dev/null 2>&1 \
      && echo "  cloudsql.client: ok (ou já existia)" \
      || echo "::warning:: falha ao conceder cloudsql.client a ${RUNTIME_SA}"

    # ---- 5.5 SA de invocação do Scheduler --------------------------------
    echo "--- SA de invocação ${SCHEDULER_SA} ---"
    if gcloud iam service-accounts describe "$SCHEDULER_SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
      echo "  já existe — ok"
    else
      gcloud iam service-accounts create "$SCHEDULER_SA_ID" \
        --display-name="Cloud Scheduler — carteira de títulos (${ENV})" \
        --project="$PROJECT_ID" --quiet >/dev/null \
        && echo "  criada" \
        || echo "::warning:: falha ao criar ${SCHEDULER_SA}"
      # Criação de SA é eventual-consistente: sem a espera, o binding abaixo
      # falha com "does not exist".
      sleep 10
    fi

    # run.invoker NO JOB (não no projeto): a SA do agendador só pode disparar
    # ESTE job, e nada mais.
    gcloud run jobs add-iam-policy-binding "$SYNC_TITLES_JOB" \
      --region="$REGION" --project="$PROJECT_ID" \
      --member="serviceAccount:${SCHEDULER_SA}" \
      --role="roles/run.invoker" \
      --quiet >/dev/null 2>&1 \
      && echo "  run.invoker em ${SYNC_TITLES_JOB}: ok (ou já existia)" \
      || echo "::warning:: falha ao conceder run.invoker em ${SYNC_TITLES_JOB} a ${SCHEDULER_SA}"

    # ---- 5.6 Cloud Scheduler --------------------------------------------
    # Dispara a API de execução do Cloud Run Jobs. É a API ADMIN do Cloud Run,
    # então o token é OAuth (escopo cloud-platform), não OIDC.
    # --max-retry-attempts=0: se o disparo falhar, o ciclo de amanhã tenta —
    # retentar agora poderia sobrepor duas execuções do mesmo cliente.
    _run_uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${SYNC_TITLES_JOB}:run"
    echo "--- Cloud Scheduler ${SYNC_TITLES_SCHEDULER} ('${SYNC_TITLES_SCHEDULE}' ${SYNC_TITLES_TZ}) ---"
    if gcloud scheduler jobs describe "$SYNC_TITLES_SCHEDULER" \
         --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
      _sched_verb=update
    else
      _sched_verb=create
    fi
    gcloud scheduler jobs "$_sched_verb" http "$SYNC_TITLES_SCHEDULER" \
      --location="$REGION" --project="$PROJECT_ID" \
      --schedule="$SYNC_TITLES_SCHEDULE" \
      --time-zone="$SYNC_TITLES_TZ" \
      --uri="$_run_uri" \
      --http-method=POST \
      --oauth-service-account-email="$SCHEDULER_SA" \
      --max-retry-attempts=0 \
      --attempt-deadline=180s \
      --description="Sincroniza a carteira de títulos em aberto (Sprint 11 R5) — ${ENV}" \
      --quiet >/dev/null \
      && echo "  ${_sched_verb} ok" \
      || echo "::warning:: falha ao ${_sched_verb} o Cloud Scheduler ${SYNC_TITLES_SCHEDULER}"
  fi
fi

# ------------------------------------------------------------------
# 6. Contrato com o backend (03.3/03.6) e o deploy — nomes canônicos
#    A prova de ENTREGA (smoke pós-deploy) NÃO cria recurso de cloud aqui: ela
#    roda no runner do CI, faz login como admin de MONITORAÇÃO e dispara o
#    gatilho SINTÉTICO da 03.6 (POST /api/v1/system/alert-test) contra o serviço
#    vivo. Isso exige um bootstrap manual (não versionado) — instruções abaixo.
# ------------------------------------------------------------------
GH_ENV="$([[ "$ENV" == "dev" ]] && echo development || echo production)"
API_URL_VAR="$([[ "$ENV" == "dev" ]] && echo API_URL_DEV || echo API_URL_PROD)"
echo ""
echo "=== ✓ Provisionamento (${ENV}) concluído ==="
echo ""
echo "1) KMS — injetado nos deploys (NÃO é segredo, é resource path):"
echo "     KEK_KMS_KEY_NAME=${KEK_KMS_KEY_NAME}"
echo ""
echo "2) Alerta — adicione o VALOR real do canal compartilhado (pelo menos UM;"
echo "   fail-closed: sem canal entregável o serviço NEM SOBE). Ex.:"
echo "     printf 'https://hooks.slack.com/...' | gcloud secrets versions add ${ALERT_WEBHOOK_SECRET} --data-file=- --project=${PROJECT_ID}"
echo "     printf 'plantao-adl@hologram.com.br' | gcloud secrets versions add ${ALERT_EMAIL_SECRET} --data-file=- --project=${PROJECT_ID}"
echo "   Os deploys montam ${ALERT_WEBHOOK_SECRET}/${ALERT_EMAIL_SECRET} como"
echo "   ALERT_WEBHOOK_URL/ALERT_EMAIL_TO no serviço da API E nos jobs (paridade)."
echo "   OBS: o canal de e-mail só entrega se ALERT_SMTP_HOST também estiver"
echo "   configurado no serviço; o webhook é o canal deliverable por padrão."
echo ""
echo "3) Smoke de ENTREGA (03.7) — bootstrap manual, 1× por ambiente:"
echo "   a) Crie um admin DEDICADO de monitoração (perfil admin, senha forte)."
echo "      Ex.: SEED_ADMIN_EMAIL=smoke-monitor@hologram.com.br \\"
echo "           SEED_ADMIN_PASSWORD='<forte>' <rodar seed/criação de usuário>"
echo "   b) No GitHub → Settings → Environments → ${GH_ENV} → Secrets, adicione:"
echo "        SMOKE_ADMIN_EMAIL     = o e-mail do admin de monitoração"
echo "        SMOKE_ADMIN_PASSWORD  = a senha dele (NUNCA no repo/log)"
echo "   c) Confirme a Variable ${API_URL_VAR} no mesmo Environment (base da API)."
echo "   O job smoke-alert loga como esse admin e dispara /system/alert-test;"
echo "   se a notificação não chegar (delivered=false), o pipeline REPROVA."
echo ""
echo "4) Carteira de títulos (Sprint 11, R5) — sincronização diária:"
echo "     Cloud Run Job:   ${SYNC_TITLES_JOB}  (python -m scripts.sync_client_titles)"
echo "     Cloud Scheduler: ${SYNC_TITLES_SCHEDULER}  ['${SYNC_TITLES_SCHEDULE}' ${SYNC_TITLES_TZ}]"
echo "     SA de invocação: ${SCHEDULER_SA}  (run.invoker só neste job)"
echo "   Confira e dispare uma execução manual de conferência:"
echo "     gcloud scheduler jobs describe ${SYNC_TITLES_SCHEDULER} --location=${REGION} --project=${PROJECT_ID}"
echo "     gcloud run jobs execute ${SYNC_TITLES_JOB} --region=${REGION} --project=${PROJECT_ID} --wait"
echo "   O deploy re-resolve a imagem deste job a cada execução — sem isso o"
echo "   cron rodaria a imagem velha pra sempre. Se algum ::warning:: apareceu"
echo "   na seção da carteira acima, o agendamento NÃO está de pé."
echo ""
