#!/bin/bash
# Teste do replay_cloud_run_manifests.sh sobre a fixture sintética de testdata/, sem
# gcloud nem Docker (subtask 86e3r2egh). Roda o dry-run com --from-dir e exige, nos 7
# manifestos reescritos: zero ocorrências do projeto, do número e das URLs antigas e de
# REDIS_URL; nenhum nome de revisão nem carimbo de operação; os valores novos no lugar;
# e, na API, minScale 1 e cpu-throttling false preservados.
#
# Uso: bash scripts/deploy/test_replay_cloud_run_manifests.sh   (saída 0 = passou)

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="${HERE}/replay_cloud_run_manifests.sh"
FIXTURE="${HERE}/testdata"
OUT="$(mktemp -d)"
trap 'rm -r "$OUT"' EXIT

NEW_KEK="projects/hologramos/locations/southamerica-east1/keyRings/auditoria-dev/cryptoKeys/omie-encryption-kek"
NEW_SQL="hologramos:southamerica-east1:hologramos-pg"
NEW_API_HOST="auditoria-api-dev-910127994973.southamerica-east1.run.app"
NEW_WEB_HOST="auditoria-web-dev-910127994973.southamerica-east1.run.app"

replay() {
  bash "$SCRIPT" \
    --source-project liberdade-assessoria \
    --target-project hologramos --target-project-number 910127994973 \
    --target-sql-connection "$NEW_SQL" --target-kek "$NEW_KEK" \
    --api-url "https://${NEW_API_HOST}" --web-url "https://${NEW_WEB_HOST}" \
    "$@"
}

failures=0
check() {
  # check <descrição> <comando…>: passa se o comando sai com 0
  local description="$1"; shift
  if "$@"; then
    echo "ok    ${description}"
  else
    echo "FALHA ${description}"
    failures=$((failures + 1))
  fi
}
absent() { ! grep -qE -- "$1" "$2"; }
present() { grep -qF -- "$1" "$2"; }
count_is() { [[ "$(grep -cF -- "$1" "$2" || true)" -eq "$3" ]]; }

# 1. O dry-run sobre a fixture passa e grava os 7 manifestos.
replay --from-dir "$FIXTURE" --out-dir "$OUT" > "${OUT}/run.log"
check "dry-run sai com 0 e anuncia as provas OK" grep -q 'todas as provas OK' "${OUT}/run.log"
manifests=()
for f in "$FIXTURE"/*.yaml; do manifests+=("${OUT}/$(basename "$f")"); done
check "7 manifestos reescritos" test "${#manifests[@]}" -eq 7

# 2. Nada da origem sobra, em nenhum manifesto.
for m in "${manifests[@]}"; do
  n="$(basename "$m" .yaml)"
  for old in 'liberdade-assessoria' '625547925581' 'i7oep34hva-rj' 'REDIS_URL' 'auditoria-redis-url' \
             '@sha256:' 'run\.googleapis\.com/(operation-id|lastModifier|creator|urls)' \
             'client\.knative\.dev/nonce' 'serving\.knative\.dev/[A-Za-z]+:' '^status:' \
             '^  (uid|resourceVersion|generation|creationTimestamp|selfLink):'; do
    check "${n}: sem ${old}" absent "$old" "$m"
  done
  check "${n}: sem spec.template.metadata.name" \
    bash -c "! grep -qE '^      name: ' '$m'"
  check "${n}: sem mapa vazio (chave sem valor seguida de irmã ou de nada)" \
    bash -c "! awk 'prev ~ /:\$/ { match(prev, /^ */); pi = RLENGTH; match(\$0, /^ */); if (RLENGTH < pi || (RLENGTH == pi && \$0 !~ /^ *- /)) bad = 1 } { prev = \$0 } END { if (prev ~ /:\$/) bad = 1; exit !bad }' '$m'"
  check "${n}: namespace do destino" present "namespace: '910127994973'" "$m"
  check "${n}: SA do destino" present '@hologramos.iam.gserviceaccount.com' "$m"
  check "${n}: imagem do destino" present 'southamerica-east1-docker.pkg.dev/hologramos/auditoria/' "$m"
done

# 3. Os valores trocados por argumento.
api="${OUT}/auditoria-api-dev.yaml"
web="${OUT}/auditoria-web-dev.yaml"
check "api: minScale '1' preservado" present "autoscaling.knative.dev/minScale: '1'" "$api"
check "api: cpu-throttling 'false' preservado" present "run.googleapis.com/cpu-throttling: 'false'" "$api"
check "api: cloudsql-instances novo" present "run.googleapis.com/cloudsql-instances: ${NEW_SQL}" "$api"
check "api: KEK nova" present "value: ${NEW_KEK}" "$api"
check "api: ALLOWED_HOSTS só host, sem duplicata" \
  present "value: ${NEW_API_HOST},localhost" "$api"
check "api: ALLOWED_ORIGINS sem duplicata" present "value: https://${NEW_WEB_HOST}" "$api"
check "api: FRONTEND_URL na web nova" count_is "https://${NEW_WEB_HOST}" "$api" 2
check "api: digest virou a tag dev" present 'auditoria/auditoria-api:dev' "$api"
check "api: secrets seguem por nome" present 'name: auditoria-jwt-secret-dev' "$api"
check "web: INTERNAL_API_URL na API nova" present "value: https://${NEW_API_HOST}" "$web"
for job in migrate cleanup-stuck sync-titles promote seed; do
  m="${OUT}/auditoria-api-${job}-dev.yaml"
  check "${job}: KEK nova" present "value: ${NEW_KEK}" "$m"
  check "${job}: cloudsql-instances novo" present "cloudsql-instances: ${NEW_SQL}" "$m"
  check "${job}: tag+digest virou só a tag" present 'auditoria-api:dev' "$m"
  check "${job}: mapa labels que ficou vazio saiu" bash -c "! grep -qE '^      labels:' '$m'"
done

# 4. Recusas antes de qualquer gcloud.
expect_exit() {
  # expect_exit <código> <descrição> <args…>
  local code="$1" description="$2"; shift 2
  set +e
  replay "$@" > /dev/null 2>&1
  local got=$?
  set -e
  check "${description} (saída ${code})" test "$got" -eq "$code"
}
expect_exit 2 "--apply com --from-dir é recusado" --from-dir "$FIXTURE" --out-dir "$OUT/x" --apply
expect_exit 2 "--out-dir dentro do repositório é recusado" --from-dir "$FIXTURE" --out-dir "${HERE}/out-test"
check "--out-dir recusado não foi criado" test ! -e "${HERE}/out-test"
expect_exit 2 "flag de valor sem valor no fim é recusada" --from-dir "$FIXTURE" --out-dir
expect_exit 2 "--target-kek de outro projeto é recusado" --from-dir "$FIXTURE" --out-dir "$OUT/y" \
  --target-kek projects/liberdade-assessoria/locations/southamerica-east1/keyRings/a/cryptoKeys/b

# 5. Um resíduo que a reescrita não conhece faz a prova falhar (saída 1).
mkdir -p "${OUT}/dirty"
cp "$FIXTURE"/*.yaml "${OUT}/dirty/"
awk '/^        - name: LOG_LEVEL$/ {
       print "        - name: STRAY"; print "          value: gs://liberdade-assessoria-bucket"
     } { print }' "$FIXTURE/auditoria-api-dev.yaml" > "${OUT}/dirty/auditoria-api-dev.yaml"
check "fixture suja tem o resíduo" present 'gs://liberdade-assessoria-bucket' "${OUT}/dirty/auditoria-api-dev.yaml"
expect_exit 1 "resíduo desconhecido do projeto antigo reprova a prova" \
  --from-dir "${OUT}/dirty" --out-dir "${OUT}/dirty-out"

echo ""
if [[ "$failures" -eq 0 ]]; then
  echo "✓ replay: todas as verificações passaram."
else
  echo "✗ replay: ${failures} verificação(ões) falharam."
  exit 1
fi
