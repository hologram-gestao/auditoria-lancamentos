#!/bin/bash
# Replica os serviços e jobs do Cloud Run de um projeto de ORIGEM para um de DESTINO
# (épico 86e3r2egb, subtask 86e3r2egh). Os recursos foram criados à mão no projeto
# antigo e o deploy-dev.yml só faz `services update`/`jobs update`: no projeto novo
# eles têm de existir antes do primeiro deploy.
#
# A técnica é a do setup-gcp.sh com o job da carteira: `describe --format=export` na
# origem, limpeza do que é carimbo de operação, troca do que aponta para o projeto
# antigo, e `services replace`/`jobs replace` no destino.
#
# O que muda em cada manifesto:
#   - sai: status, metadata.{uid,resourceVersion,generation,creationTimestamp,selfLink},
#     spec.template.metadata.name (nome de revisão) e, em annotations e labels, as chaves
#     run.googleapis.com/{operation-id,lastModifier,creator,urls}, client.knative.dev/nonce
#     e serving.knative.dev/*;
#   - sai: o env REDIS_URL (legado da FASE 0);
#   - troca: metadata.namespace pelo número do destino; o projeto no Artifact Registry
#     (<AR>/<origem>/auditoria/… → <AR>/<destino>/auditoria/…, imagem por digest vira a
#     tag --image-tag, porque o build no destino gera outro digest); o domínio das SAs;
#     a annotation run.googleapis.com/cloudsql-instances (--target-sql-connection); o env
#     KEK_KMS_KEY_NAME (--target-kek); os hosts antigos da API e da web, nas duas formas
#     (*-<hash>-rj.a.run.app e *-<número>.southamerica-east1.run.app), por --api-url e
#     --web-url, com duplicata removida em ALLOWED_HOSTS e ALLOWED_ORIGINS.
#
# Depois da reescrita, uma PROVA por recurso: zero ocorrências do id e do número da
# origem, dos hosts antigos e de REDIS_URL, sem nome de revisão, sem tráfego preso a
# revisão e sem digest. Prova que falha = saída 1 e nada é aplicado.
#
# A origem só é LIDA (describe). Nenhum valor de secret passa por aqui: os manifestos
# só têm NOMES de secret (secretKeyRef).
#
# Uso (dry-run, o padrão: só grava os manifestos e mostra diff e prova):
#   bash scripts/deploy/replay_cloud_run_manifests.sh \
#     --source-project liberdade-assessoria \
#     --target-project hologramos-512320 --target-project-number 114342061812 \
#     --target-sql-connection hologramos-512320:southamerica-east1:<instância> \
#     --target-kek projects/hologramos-512320/locations/southamerica-east1/keyRings/auditoria-dev/cryptoKeys/omie-encryption-kek \
#     --api-url https://auditoria-api-dev-114342061812.southamerica-east1.run.app \
#     --web-url https://auditoria-web-dev-114342061812.southamerica-east1.run.app \
#     --out-dir /tmp/replay-hologramos-512320
#
# Opcionais:
#   --apply           aplica no destino (replace um por vez, para no primeiro erro).
#                     Exige as imagens já no Artifact Registry do destino.
#   --from-dir <dir>  lê <dir>/<recurso>.yaml no lugar do describe da origem (teste com
#                     a fixture de scripts/deploy/testdata/). Não combina com --apply.
#   --image-tag <t>   tag usada no lugar de um digest (default dev, a do deploy-dev.yml).
#   --region <r>      default southamerica-east1.
#   --api-ingress <i> ingresso da API no destino: internal (default) ou all. A API nova
#                     nasce fechada: até o --verify do reembrulho das DEKs zerar, ela não
#                     recebe tráfego público (runbook, passo 7). A web mantém o da origem.
#
# O --apply exige um dry-run anterior no MESMO --out-dir e só aplica se a origem, lida de
# novo, gerar exatamente o manifesto revisado; se ela mudou no meio, para sem aplicar.
#
# Saídas: 0 ok · 1 alguma prova falhou ou o replace falhou · 2 argumento inválido.

set -euo pipefail

SERVICES=(auditoria-api-dev auditoria-web-dev)
JOBS=(
  auditoria-api-migrate-dev
  auditoria-api-cleanup-stuck-dev
  auditoria-api-sync-titles-dev
  auditoria-api-promote-dev
  auditoria-api-seed-dev
)
AR_HOST="southamerica-east1-docker.pkg.dev"
AR_REPO="auditoria"

SOURCE_PROJECT=""
TARGET_PROJECT=""
TARGET_PROJECT_NUMBER=""
TARGET_SQL_CONNECTION=""
TARGET_KEK=""
API_URL=""
WEB_URL=""
OUT_DIR=""
FROM_DIR=""
IMAGE_TAG="dev"
REGION="southamerica-east1"
API_INGRESS="internal"
APPLY=0

usage() {
  sed -n '2,/^set -euo pipefail/p' "$0" | sed -e '/^set -euo/d' -e 's/^# \{0,1\}//'
}

die() {
  echo "::error:: $*" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  # Flag de valor sem valor (no fim da linha ou seguida de outra flag): o `shift 2`
  # sairia calado sob set -e, ou a flag seguinte viraria o valor.
  if [[ "$1" == --* && "$1" != --apply && "$1" != --help ]] \
     && [[ $# -lt 2 || "${2:-}" == --* ]]; then
    die "$1 precisa de um valor."
  fi
  case "$1" in
    --source-project) SOURCE_PROJECT="${2:-}"; shift 2 ;;
    --target-project) TARGET_PROJECT="${2:-}"; shift 2 ;;
    --target-project-number) TARGET_PROJECT_NUMBER="${2:-}"; shift 2 ;;
    --target-sql-connection) TARGET_SQL_CONNECTION="${2:-}"; shift 2 ;;
    --target-kek) TARGET_KEK="${2:-}"; shift 2 ;;
    --api-url) API_URL="${2:-}"; shift 2 ;;
    --web-url) WEB_URL="${2:-}"; shift 2 ;;
    --out-dir) OUT_DIR="${2:-}"; shift 2 ;;
    --from-dir) FROM_DIR="${2:-}"; shift 2 ;;
    --image-tag) IMAGE_TAG="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --api-ingress) API_INGRESS="${2:-}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "argumento desconhecido: $1 (veja --help)" ;;
  esac
done

# ------------------------------------------------------------------
# Validação dos argumentos (antes de qualquer chamada ao gcloud)
# ------------------------------------------------------------------
_project_id_re='^[a-z][a-z0-9-]{4,28}[a-z0-9]$'
[[ "$SOURCE_PROJECT" =~ $_project_id_re ]] || die "--source-project ausente ou inválido."
[[ "$TARGET_PROJECT" =~ $_project_id_re ]] || die "--target-project ausente ou inválido."
[[ "$SOURCE_PROJECT" != "$TARGET_PROJECT" ]] || die "origem e destino são o mesmo projeto."
[[ "$TARGET_PROJECT_NUMBER" =~ ^[0-9]+$ ]] || die "--target-project-number ausente ou não numérico."
[[ "$TARGET_SQL_CONNECTION" =~ ^${TARGET_PROJECT}:[a-z0-9-]+:[a-z0-9-]+$ ]] \
  || die "--target-sql-connection tem de ser <destino>:<região>:<instância> do projeto ${TARGET_PROJECT}."
[[ "$TARGET_KEK" =~ ^projects/${TARGET_PROJECT}/locations/[a-z0-9-]+/keyRings/[A-Za-z0-9_-]+/cryptoKeys/[A-Za-z0-9_-]+$ ]] \
  || die "--target-kek tem de ser projects/${TARGET_PROJECT}/locations/<r>/keyRings/<k>/cryptoKeys/<c>."
[[ "$API_URL" =~ ^https://[a-z0-9.-]+$ ]] || die "--api-url tem de ser https://<host>, sem caminho nem barra final."
[[ "$WEB_URL" =~ ^https://[a-z0-9.-]+$ ]] || die "--web-url tem de ser https://<host>, sem caminho nem barra final."
[[ "$API_INGRESS" == internal || "$API_INGRESS" == all ]] || die "--api-ingress tem de ser internal ou all."
[[ "$REGION" =~ ^[a-z]+-[a-z]+[0-9]+$ ]] || die "--region inválida."
[[ "$IMAGE_TAG" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]*$ ]] || die "--image-tag inválida."
[[ -n "$OUT_DIR" ]] || die "--out-dir é obrigatório (fora do repositório)."
if [[ -n "$FROM_DIR" ]]; then
  [[ -d "$FROM_DIR" ]] || die "--from-dir ${FROM_DIR} não é um diretório."
  [[ "$APPLY" -eq 0 ]] || die "--apply não combina com --from-dir: fixture nunca vai para o destino."
fi

# Os manifestos não entram no repositório: o --out-dir tem de ficar fora dele.
# A conferência vem antes do mkdir: recusado, o diretório nem chega a existir.
_repo_root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null || true)"
_out_abs="$(realpath -m "$OUT_DIR")"
if [[ -n "$_repo_root" ]]; then
  _repo_abs="$(realpath "$_repo_root")"
  case "$_out_abs/" in
    "$_repo_abs"/*) die "--out-dir ${OUT_DIR} está dentro do repositório (${_repo_abs})." ;;
  esac
fi
# Permissão só no diretório que o script cria: um --out-dir existente (o $HOME, /tmp)
# fica como está.
if [[ ! -d "$_out_abs" ]]; then
  mkdir -p "$_out_abs"
  chmod 700 "$_out_abs"
fi

API_HOST="${API_URL#https://}"
WEB_HOST="${WEB_URL#https://}"

# ------------------------------------------------------------------
# Reescrita estrutural. YAML do `--format=export` do gcloud: indentação de 2 espaços e
# itens de lista no MESMO nível da chave-mãe. O awk mantém a pilha de chaves por
# indentação e apaga um bloco inteiro (a chave e o que estiver mais indentado, ou a
# lista no mesmo nível) quando o caminho casa com o que sai.
#
# mode=rewrite imprime o manifesto reescrito; mode=paths imprime o caminho pontuado de
# cada chave (usado pela prova e pelo teste).
# ------------------------------------------------------------------
# O awk lê tudo pelo ENVIRON: `awk -v` interpretaria as barras invertidas das regex.
# shellcheck disable=SC2016 # o programa é awk, as variáveis são dele
AWK_PROGRAM='
function indent_of(s) { match(s, /^ */); return RLENGTH }
function key_of(s,   k) {
  k = s
  sub(/^ *(- )?/, "", k)
  if (k !~ /^[^ :][^:]*:( |$)/) return ""
  sub(/:( .*)?$/, "", k)
  return k
}
function path_upto(n,   i, p) {
  p = ""
  for (i = 1; i <= n; i++) if (stack_key[i] != "-") p = (p == "" ? stack_key[i] : p "." stack_key[i])
  return p
}
function push(ind, k) {
  while (depth > 0 && stack_ind[depth] >= ind) depth--
  depth++; stack_ind[depth] = ind; stack_key[depth] = k
}
function unquote(v) {
  if (v ~ ("^" SQ ".*" SQ "$") || v ~ /^".*"$/) return substr(v, 2, length(v) - 2)
  return v
}
function dedup_csv(v,   n, parts, i, seen, out, item) {
  n = split(v, parts, ",")
  out = ""
  for (i = 1; i <= n; i++) {
    item = parts[i]; gsub(/^ +| +$/, "", item)
    if (item == "" || (item in seen)) continue
    seen[item] = 1
    out = (out == "" ? item : out "," item)
  }
  return out
}
# Chave sem valor na linha (mapa ou lista) fica pendente até um filho ser impresso: um
# mapa que perdeu todas as chaves (labels só com o nonce) some, em vez de virar null.
function emit(s, ind, is_item,   i) {
  while (np > 0 && !(ind > pend_eff[np] || (ind == pend_eff[np] && is_item))) np--
  if (s ~ /:$/) {
    np++; pend_line[np] = s; pend_eff[np] = (is_item ? ind + 2 : ind)
    return
  }
  for (i = 1; i <= np; i++) print pend_line[i]
  np = 0
  print s
}
function rewrite_globals(s) {
  gsub(E["RP_AR_SRC"], E["RP_AR_DST"], s)
  gsub(E["RP_SA_SRC"], E["RP_SA_DST"], s)
  gsub(E["RP_API_OLD1"], E["RP_API_HOST"], s); gsub(E["RP_API_OLD2"], E["RP_API_HOST"], s)
  gsub(E["RP_WEB_OLD1"], E["RP_WEB_HOST"], s); gsub(E["RP_WEB_OLD2"], E["RP_WEB_HOST"], s)
  return s
}
BEGIN {
  SQ = sprintf("%c", 39)
  split("RP_MODE RP_AR_SRC RP_AR_DST RP_SA_SRC RP_SA_DST RP_API_OLD1 RP_API_OLD2 RP_WEB_OLD1 RP_WEB_OLD2 RP_API_HOST RP_WEB_HOST RP_TARGET_NUMBER RP_SQL_CONNECTION RP_TARGET_KEK RP_IMAGE_TAG RP_INGRESS", names, " ")
  for (i in names) E[names[i]] = ENVIRON[names[i]]
  rewrite = (E["RP_MODE"] == "rewrite")
  np = 0; depth = 0; skip_ind = -1; skip_list = 0; env_ind = -1; env_name = ""
  drop_path["status"] = 1
  drop_path["metadata.uid"] = 1; drop_path["metadata.resourceVersion"] = 1
  drop_path["metadata.generation"] = 1; drop_path["metadata.creationTimestamp"] = 1
  drop_path["metadata.selfLink"] = 1
  drop_path["spec.template.metadata.name"] = 1
  drop_key_re = "^(run\\.googleapis\\.com/(operation-id|lastModifier|creator|urls|ingress-status)|client\\.knative\\.dev/nonce|serving\\.knative\\.dev/.*)$"
}
{
  line = $0
  if (line ~ /^ *$/) next
  ind = indent_of(line)
  is_item = (substr(line, ind + 1, 2) == "- ")

  # Dentro de um bloco removido: a chave e o que estiver mais indentado (ou a lista
  # dela, que o gcloud escreve no MESMO nível da chave).
  if (skip_ind >= 0) {
    if (ind > skip_ind || (skip_list && ind == skip_ind && is_item)) next
    skip_ind = -1; skip_list = 0
  }
  if (env_ind >= 0 && ind <= env_ind) { env_ind = -1; env_name = "" }

  k = key_of(line)
  if (is_item) {
    while (depth > 0 && (stack_ind[depth] > ind || (stack_ind[depth] == ind && stack_key[depth] == "-"))) depth--
    depth++; stack_ind[depth] = ind; stack_key[depth] = "-"
    if (k != "") push(ind + 2, k)
    if (k == "name" && depth >= 3 && stack_key[depth - 2] == "env") {
      item_value = line; sub(/^ *- name: */, "", item_value); item_value = unquote(item_value)
      if (rewrite && item_value == "REDIS_URL") { skip_ind = ind; skip_list = 0; depth -= 2; next }
      env_ind = ind; env_name = item_value
    }
  } else if (k != "") {
    push(ind, k)
  } else {
    if (rewrite) emit(rewrite_globals(line), ind, is_item)
    next
  }

  p = path_upto(depth)
  # O modo paths descreve o arquivo como ele está: é o que a prova confere.
  if (!rewrite) { print p; next }

  parent = path_upto(depth - 1)
  if ((p in drop_path) || (parent ~ /(^|\.)(annotations|labels)$/ && k ~ drop_key_re)) {
    skip_ind = (is_item ? ind + 2 : ind); skip_list = 1
    depth--
    next
  }

  pad = substr(line, 1, ind)
  if (p == "metadata.namespace") { emit(pad "namespace: " SQ E["RP_TARGET_NUMBER"] SQ, ind, 0); next }
  if (p == "metadata.annotations.run.googleapis.com/ingress" && E["RP_INGRESS"] != "") {
    emit(pad k ": " E["RP_INGRESS"], ind, 0); next
  }
  if (k == "run.googleapis.com/cloudsql-instances" && parent ~ /annotations$/) {
    emit(pad k ": " E["RP_SQL_CONNECTION"], ind, 0); next
  }
  if (env_ind >= 0 && !is_item && ind == env_ind + 2 && k == "value") {
    if (env_name == "KEK_KMS_KEY_NAME") { emit(pad "value: " E["RP_TARGET_KEK"], ind, 0); next }
    if (env_name == "ALLOWED_HOSTS" || env_name == "ALLOWED_ORIGINS") {
      v = line; sub(/^ *value: */, "", v)
      emit(pad "value: " dedup_csv(rewrite_globals(unquote(v))), ind, 0)
      next
    }
  }
  if (k == "image" || k == "client.knative.dev/user-image") {
    line = rewrite_globals(line)
    # Digest da origem não existe no destino (o build lá gera outro): vira a tag.
    sub(/(:[A-Za-z0-9_.-]+)?@sha256:[0-9a-f]+/, ":" E["RP_IMAGE_TAG"], line)
    emit(line, ind, is_item); next
  }
  emit(rewrite_globals(line), ind, is_item)
}
'

run_awk() {
  # $1 = modo (rewrite | paths), $2 = arquivo, $3 = número do projeto de origem,
  # $4 = ingresso a gravar (vazio = o da origem)
  local source_number="$3"
  RP_MODE="$1" \
  RP_AR_SRC="${AR_HOST//./\\.}/${SOURCE_PROJECT}/${AR_REPO}/" \
  RP_AR_DST="${AR_HOST}/${TARGET_PROJECT}/${AR_REPO}/" \
  RP_SA_SRC="@${SOURCE_PROJECT}\\.iam\\.gserviceaccount\\.com" \
  RP_SA_DST="@${TARGET_PROJECT}.iam.gserviceaccount.com" \
  RP_API_OLD1='auditoria-api-dev-[a-z0-9]+-rj\.a\.run\.app' \
  RP_API_OLD2="auditoria-api-dev-${source_number}\\.${REGION//./\\.}\\.run\\.app" \
  RP_WEB_OLD1='auditoria-web-dev-[a-z0-9]+-rj\.a\.run\.app' \
  RP_WEB_OLD2="auditoria-web-dev-${source_number}\\.${REGION//./\\.}\\.run\\.app" \
  RP_API_HOST="$API_HOST" RP_WEB_HOST="$WEB_HOST" \
  RP_TARGET_NUMBER="$TARGET_PROJECT_NUMBER" \
  RP_SQL_CONNECTION="$TARGET_SQL_CONNECTION" \
  RP_TARGET_KEK="$TARGET_KEK" \
  RP_IMAGE_TAG="$IMAGE_TAG" \
  RP_INGRESS="${4:-}" \
    awk "$AWK_PROGRAM" "$2"
}

# ------------------------------------------------------------------
# Prova: o manifesto reescrito não aponta para nada da origem
# ------------------------------------------------------------------
prove() {
  local name="$1" file="$2" source_number="$3" ok=0 count
  echo "  prova:"
  for pattern in "$SOURCE_PROJECT" "$source_number" '-rj\.a\.run\.app' 'REDIS_URL' '@sha256:'; do
    count="$(grep -cE -- "$pattern" "$file" || true)"
    if [[ "$count" -eq 0 ]]; then
      echo "    ok    0 ocorrências de ${pattern}"
    else
      echo "    FALHA ${count} ocorrência(s) de ${pattern}:"
      grep -nE -- "$pattern" "$file" | sed 's/^/            /'
      ok=1
    fi
  done
  if run_awk paths "$file" "$source_number" | grep -qx 'spec.template.metadata.name'; then
    echo "    FALHA spec.template.metadata.name (nome de revisão) continua no manifesto"
    ok=1
  else
    echo "    ok    sem spec.template.metadata.name"
  fi
  if grep -qE '^ *(- )?revisionName:' "$file"; then
    echo "    FALHA spec.traffic aponta para uma revisão da origem (revisionName)"
    ok=1
  fi
  if [[ "$name" == auditoria-api-dev ]] \
     && ! grep -qxE "    run\.googleapis\.com/ingress: ${API_INGRESS}" "$file"; then
    echo "    FALHA a API não ficou com ingress ${API_INGRESS}"
    ok=1
  fi
  if ! grep -qF "$TARGET_PROJECT" "$file"; then
    echo "    FALHA nenhuma referência a ${TARGET_PROJECT} (imagem e SA deveriam apontar para ele)"
    ok=1
  fi
  if [[ "$ok" -eq 0 ]]; then
    echo "  ${name}: prova OK"
  else
    echo "  ${name}: prova FALHOU"
  fi
  return "$ok"
}

echo "=== replay Cloud Run: ${SOURCE_PROJECT} → ${TARGET_PROJECT} (${TARGET_PROJECT_NUMBER}) ==="
if [[ -n "$FROM_DIR" ]]; then
  echo "    origem: arquivos em ${FROM_DIR} (sem gcloud)"
fi
echo "    saída:  ${_out_abs}"
echo "    modo:   $([[ "$APPLY" -eq 1 ]] && echo APPLY || echo dry-run)"
echo ""

declare -a KINDS=()
declare -a NAMES=()
for _s in "${SERVICES[@]}"; do KINDS+=(services); NAMES+=("$_s"); done
for _j in "${JOBS[@]}"; do KINDS+=(jobs); NAMES+=("$_j"); done

failed=0
for i in "${!NAMES[@]}"; do
  kind="${KINDS[$i]}"
  name="${NAMES[$i]}"
  src_file="${_out_abs}/${name}.source.yaml"
  dst_file="${_out_abs}/${name}.yaml"
  echo "--- ${kind%s} ${name} ---"

  # No --apply, o que vai para o destino tem de ser o que foi revisado no dry-run.
  if [[ "$APPLY" -eq 1 ]]; then
    [[ -f "$dst_file" ]] || {
      echo "::error:: ${dst_file} não existe: rode o dry-run neste --out-dir e revise antes do --apply." >&2
      exit 1
    }
    cp "$dst_file" "${dst_file}.reviewed"
  fi

  if [[ -n "$FROM_DIR" ]]; then
    [[ -f "${FROM_DIR}/${name}.yaml" ]] || { echo "::error:: ${FROM_DIR}/${name}.yaml não existe" >&2; exit 2; }
    cp "${FROM_DIR}/${name}.yaml" "$src_file"
  else
    gcloud run "$kind" describe "$name" \
      --region="$REGION" --project="$SOURCE_PROJECT" --format=export > "$src_file"
  fi

  # O namespace do manifesto É o número do projeto de origem.
  source_number="$(sed -n "s/^  namespace: *['\"]\{0,1\}\([0-9][0-9]*\)['\"]\{0,1\} *$/\1/p" "$src_file" | head -n 1)"
  [[ -n "$source_number" ]] || { echo "::error:: ${name}: metadata.namespace numérico não encontrado" >&2; exit 2; }

  ingress=""
  [[ "$name" == auditoria-api-dev ]] && ingress="$API_INGRESS"

  run_awk rewrite "$src_file" "$source_number" "$ingress" > "$dst_file"

  if [[ "$APPLY" -eq 1 ]] && ! cmp -s "${dst_file}.reviewed" "$dst_file"; then
    echo "::error:: ${name} mudou na origem desde o dry-run (diff abaixo): nada foi aplicado." >&2
    diff -u "${dst_file}.reviewed" "$dst_file" | sed 's/^/    /' >&2 || true
    exit 1
  fi

  echo "  diff -u origem destino:"
  diff -u "$src_file" "$dst_file" | sed 's/^/    /' || true
  if ! prove "$name" "$dst_file" "$source_number"; then
    failed=1
  fi
  echo ""
done

if [[ "$failed" -ne 0 ]]; then
  echo "::error:: pelo menos uma prova falhou: nada foi aplicado. Corrija e rode de novo." >&2
  exit 1
fi

if [[ "$APPLY" -eq 0 ]]; then
  echo "✓ dry-run: ${#NAMES[@]} manifestos em ${_out_abs}, todas as provas OK. Rode com --apply para aplicar."
  exit 0
fi

echo "=== Aplicando em ${TARGET_PROJECT} (um por vez, para no primeiro erro) ==="
for i in "${!NAMES[@]}"; do
  kind="${KINDS[$i]}"
  name="${NAMES[$i]}"
  echo "--- gcloud run ${kind} replace ${name} ---"
  if ! gcloud run "$kind" replace "${_out_abs}/${name}.yaml" \
         --region="$REGION" --project="$TARGET_PROJECT" --quiet; then
    echo "::error:: replace de ${name} falhou; os anteriores foram aplicados, os seguintes não." >&2
    exit 1
  fi
done
echo ""
echo "✓ ${#NAMES[@]} recursos aplicados em ${TARGET_PROJECT}."
