#!/bin/bash
# Teste do replay_cloud_run_manifests.sh sobre a fixture sintética de testdata/, sem
# gcloud nem Docker (subtask 86e3r2egh). Roda o dry-run com --from-dir e exige, nos 7
# manifestos reescritos: YAML válido (parse com python3 + PyYAML); zero ocorrências do
# projeto, do número e das URLs antigas e de REDIS_URL; nenhum nome de revisão nem
# carimbo de operação; os valores novos no lugar, com ALLOWED_HOSTS e ALLOWED_ORIGINS
# entre aspas simples; e, na API, minScale 1 e cpu-throttling false preservados. Os casos
# negativos: manifesto que não é YAML e python3 sem PyYAML reprovam (saída 1).
#
# Uso: bash scripts/deploy/test_replay_cloud_run_manifests.sh   (saída 0 = passou)

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="${HERE}/replay_cloud_run_manifests.sh"
FIXTURE="${HERE}/testdata"
OUT="$(mktemp -d)"
trap 'rm -r "$OUT"' EXIT

NEW_KEK="projects/hologramos-512320/locations/southamerica-east1/keyRings/auditoria-dev/cryptoKeys/omie-encryption-kek"
NEW_SQL="hologramos-512320:southamerica-east1:hologramos-pg"
NEW_API_HOST="auditoria-api-dev-114342061812.southamerica-east1.run.app"
NEW_WEB_HOST="auditoria-web-dev-114342061812.southamerica-east1.run.app"

replay() {
  bash "$SCRIPT" \
    --source-project liberdade-assessoria \
    --target-project hologramos-512320 --target-project-number 114342061812 \
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
yaml_ok() { python3 -I -c 'import sys, yaml; sys.exit(not isinstance(yaml.safe_load(open(sys.argv[1])), dict))' "$1" 2> /dev/null; }
# env_value <manifesto> <nome>: o valor do env como o YAML o lê (depois do parse)
env_value() {
  python3 -I -c '
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1]))
spec = doc["spec"]["template"]["spec"]
spec = spec["template"]["spec"] if "template" in spec else spec
for env in spec["containers"][0].get("env", []):
    if env["name"] == sys.argv[2]:
        print(env["value"])
' "$1" "$2"
}

# 1. O dry-run sobre a fixture passa e grava os 7 manifestos.
replay --from-dir "$FIXTURE" --out-dir "$OUT" > "${OUT}/run.log"
check "dry-run sai com 0 e anuncia as provas OK" grep -q 'todas as provas OK' "${OUT}/run.log"
manifests=()
for f in "$FIXTURE"/*.yaml; do manifests+=("${OUT}/$(basename "$f")"); done
check "7 manifestos reescritos" test "${#manifests[@]}" -eq 7

# 2. Nada da origem sobra, em nenhum manifesto.
for m in "${manifests[@]}"; do
  n="$(basename "$m" .yaml)"
  check "${n}: é YAML válido (parse)" yaml_ok "$m"
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
  check "${n}: namespace do destino" present "namespace: '114342061812'" "$m"
  check "${n}: SA do destino" present '@hologramos-512320.iam.gserviceaccount.com' "$m"
  check "${n}: imagem do destino" present 'southamerica-east1-docker.pkg.dev/hologramos-512320/auditoria/' "$m"
done

# 3. Os valores trocados por argumento.
api="${OUT}/auditoria-api-dev.yaml"
web="${OUT}/auditoria-web-dev.yaml"
check "api: minScale '1' preservado" present "autoscaling.knative.dev/minScale: '1'" "$api"
check "api: cpu-throttling 'false' preservado" present "run.googleapis.com/cpu-throttling: 'false'" "$api"
check "api: nasce com ingress internal" present "    run.googleapis.com/ingress: internal" "$api"
check "api: sem ingress-status (campo de saída)" absent 'ingress-status' "$api"
check "web: mantém o ingress da origem" present "    run.googleapis.com/ingress: all" "$web"
check "api: cloudsql-instances novo" present "run.googleapis.com/cloudsql-instances: ${NEW_SQL}" "$api"
check "api: KEK nova" present "value: ${NEW_KEK}" "$api"
check "fixture: ALLOWED_HOSTS da origem começa com * entre aspas" \
  present "value: '*.run.app," "${FIXTURE}/auditoria-api-dev.yaml"
check "api: ALLOWED_HOSTS entre aspas, só host, sem duplicata" \
  present "value: '*.run.app,${NEW_API_HOST},localhost,127.0.0.1'" "$api"
check "api: ALLOWED_HOSTS lido pelo YAML como o texto, não alias" \
  test "$(env_value "$api" ALLOWED_HOSTS)" = "*.run.app,${NEW_API_HOST},localhost,127.0.0.1"
check "api: ALLOWED_ORIGINS entre aspas, sem duplicata" present "value: 'https://${NEW_WEB_HOST}'" "$api"
check "api: ALLOWED_ORIGINS lido pelo YAML" \
  test "$(env_value "$api" ALLOWED_ORIGINS)" = "https://${NEW_WEB_HOST}"
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
expect_exit 2 "flag de valor seguida de outra flag é recusada" --from-dir "$FIXTURE" --out-dir --apply
expect_exit 2 "--api-ingress fora do vocabulário é recusado" --from-dir "$FIXTURE" --out-dir "$OUT/z" --api-ingress public
expect_exit 1 "--apply sem dry-run no mesmo --out-dir para antes do gcloud" --out-dir "$OUT/sem-dry-run" --apply
mkdir -m 755 "${OUT}/existente"
replay --from-dir "$FIXTURE" --out-dir "${OUT}/existente" > /dev/null
check "--out-dir que já existia mantém a permissão" test "$(stat -c %a "${OUT}/existente")" = 755
replay --from-dir "$FIXTURE" --out-dir "${OUT}/novo" > /dev/null
check "--out-dir criado pelo script nasce 700" test "$(stat -c %a "${OUT}/novo")" = 700
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

# 6. Aspa simples dentro do valor volta dobrada (a regra do YAML), e o parse lê uma só.
mkdir -p "${OUT}/quote"
cp "$FIXTURE"/*.yaml "${OUT}/quote/"
sed "s|^          value: https://auditoria-web-dev-i7oep34hva-rj.a.run.app,.*|          value: 'https://a''b.example,https://a''b.example'|" \
  "$FIXTURE/auditoria-api-dev.yaml" > "${OUT}/quote/auditoria-api-dev.yaml"
check "fixture com aspa interna tem o valor" present "'https://a''b.example," "${OUT}/quote/auditoria-api-dev.yaml"
replay --from-dir "${OUT}/quote" --out-dir "${OUT}/quote-out" > /dev/null
check "aspa interna sai dobrada e sem duplicata" \
  present "value: 'https://a''b.example'" "${OUT}/quote-out/auditoria-api-dev.yaml"
check "aspa interna lida pelo YAML como uma" \
  test "$(env_value "${OUT}/quote-out/auditoria-api-dev.yaml" ALLOWED_ORIGINS)" = "https://a'b.example"

# 7. Manifesto que não é YAML reprova a prova (saída 1), com o nome do recurso. O valor
#    sem aspas que começa com * passa direto pela reescrita: é o defeito de 22/10/2026
#    visto do outro lado, e o grep da prova sozinho não o pegaria.
mkdir -p "${OUT}/invalid"
cp "$FIXTURE"/*.yaml "${OUT}/invalid/"
awk '/^        - name: LOG_LEVEL$/ {
       print "        - name: STRAY"; print "          value: *.run.app,localhost"
     } { print }' "$FIXTURE/auditoria-api-dev.yaml" > "${OUT}/invalid/auditoria-api-dev.yaml"
check "fixture inválida não faz parse" bash -c "! python3 -I -c 'import sys, yaml; yaml.safe_load(open(sys.argv[1]))' '${OUT}/invalid/auditoria-api-dev.yaml' 2> /dev/null"
set +e
replay --from-dir "${OUT}/invalid" --out-dir "${OUT}/invalid-out" > "${OUT}/invalid.log" 2>&1
invalid_exit=$?
set -e
check "manifesto inválido reprova a prova (saída 1)" test "$invalid_exit" -eq 1
check "a falha diz que o manifesto da API não é YAML" \
  grep -q 'FALHA auditoria-api-dev: o manifesto reescrito não é YAML válido' "${OUT}/invalid.log"
check "os outros 6 manifestos seguem com YAML válido" \
  test "$(grep -c 'ok    YAML válido' "${OUT}/invalid.log")" -eq 6
check "nada anunciado como aplicado" bash -c "! grep -q 'todas as provas OK' '${OUT}/invalid.log'"

# 8. Sem PyYAML a prova não roda calada: o script para antes de ler a origem (saída 1).
mkdir -p "${OUT}/fakebin"
printf '#!/bin/sh\nexit 1\n' > "${OUT}/fakebin/python3"
chmod +x "${OUT}/fakebin/python3"
set +e
PATH="${OUT}/fakebin:${PATH}" replay --from-dir "$FIXTURE" --out-dir "${OUT}/no-yaml-out" > "${OUT}/no-yaml.log" 2>&1
no_yaml_exit=$?
set -e
check "python3 sem PyYAML para o script (saída 1)" test "$no_yaml_exit" -eq 1
check "a mensagem diz que falta o PyYAML" grep -q 'PyYAML não disponível' "${OUT}/no-yaml.log"
check "sem PyYAML nenhum manifesto foi gravado" test ! -e "${OUT}/no-yaml-out"

echo ""
if [[ "$failures" -eq 0 ]]; then
  echo "✓ replay: todas as verificações passaram."
else
  echo "✗ replay: ${failures} verificação(ões) falharam."
  exit 1
fi
