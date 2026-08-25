#!/bin/bash
# Roda LOCALMENTE o mesmo gate de a11y que o CI executa no job `web_a11y`
# (`.github/workflows/ci.yml`): `apps/web/e2e/a11y-mocked.spec.ts` contra o
# servidor de PRODUÇÃO do Next (build standalone), com a API interceptada no
# browser pelo próprio spec (`page.route`).
#
# Por que existe: quando o CI reprovar, o dev precisa reproduzir a falha com o
# MESMO comando — sem isso o ciclo vira "muda e torce". Os passos aqui são
# espelho 1:1 dos steps do job; se um mudar, mude os dois.
#
# NÃO precisa de Postgres, seed, API no ar nem credenciais. A suíte irmã
# `e2e/a11y.spec.ts` (ambiente completo) continua sendo outra coisa e NÃO roda
# aqui de propósito: sem `E2E_PASSWORD`/`E2E_CLIENT_ID` ela faz `test.skip` e
# deixaria o gate verde sem ter medido nada.
#
# Uso:
#   bash scripts/a11y-gate.sh                    # porta 3100, TODOS os temas (claro, escuro, hologram)
#   A11Y_THEME=dark bash scripts/a11y-gate.sh    # um tema só (light | dark | hologram | both)
#   A11Y_PORT=3200 bash scripts/a11y-gate.sh
#
# Temas (86e2n39hb): a suíte roda uma vez POR TEMA — `E2E_THEME` entra no spec
# via localStorage antes do primeiro paint. O padrão é `both`: o escuro sem
# medição foi exatamente o buraco que esta task fechou, e a task da paleta
# (86e2ukrc9) herda este gate como instrumento. Relatórios separados por tema,
# gravados DENTRO de `test-results/` (86e2w8xpv): diretório já ignorado pelo
# `.gitignore` da RAIZ, então o artefato não tem como voltar a entrar num
# commit — em nenhuma cópia do repo, worktree de agent incluído. Efeito
# colateral aceito: o Playwright limpa `test-results/` no início de CADA run,
# então ao fim do gate só o relatório do ÚLTIMO tema sobrevive — o guard lê
# cada um logo após o seu run, antes de o tema seguinte apagar (os screenshots
# ficam em `a11y-shots/<tema>/`, fora, pelo mesmo motivo).
#
# Pré-requisito de máquina: as libs de sistema do Chromium. Se o browser baixar
# mas não subir (`libnspr4.so: cannot open shared object file`), rode uma vez:
#   pnpm --filter @auditoria/web exec playwright install --with-deps chromium
# (exige root/apt). Sem root, use o container que já traz as libs:
#   docker run --rm -it -v "$PWD":/w -w /w mcr.microsoft.com/playwright:v1.48.0-jammy \
#     bash scripts/a11y-gate.sh
set -euo pipefail

PORT="${A11Y_PORT:-3100}"
BASE_URL="http://127.0.0.1:${PORT}"
SPEC="e2e/a11y-mocked.spec.ts"

case "${A11Y_THEME:-both}" in
  light) THEMES="light" ;;
  dark) THEMES="dark" ;;
  hologram) THEMES="hologram" ;;
  both) THEMES="light dark hologram" ;;
  *)
    echo "ERRO: A11Y_THEME deve ser light, dark, hologram ou both (recebi '${A11Y_THEME}')." >&2
    exit 1
    ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f "apps/web/${SPEC}" ]]; then
  echo "ERRO: apps/web/${SPEC} não existe — o gate de a11y não tem o que rodar." >&2
  exit 1
fi

SERVER_PID=""
cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "==> Chromium + libs de sistema"
pnpm --filter @auditoria/web exec playwright install --with-deps chromium

# `INTERNAL_API_URL` é resolvida pelo `next build` (rewrite estático), não em
# runtime. Aqui ela nunca é exercida: o spec intercepta `**/api/v1/**` no browser.
echo "==> Build standalone"
INTERNAL_API_URL="http://127.0.0.1:8000" pnpm --filter @auditoria/web build

# `output: standalone` do Next NÃO empacota `.next/static` nem `public/`.
echo "==> Assets do standalone"
rm -rf apps/web/.next/standalone/apps/web/.next/static
cp -r apps/web/.next/static apps/web/.next/standalone/apps/web/.next/static
if [[ -d apps/web/public ]]; then
  rm -rf apps/web/.next/standalone/apps/web/public
  cp -r apps/web/public apps/web/.next/standalone/apps/web/public
fi

echo "==> Servidor de produção em ${BASE_URL}"
PORT="$PORT" HOSTNAME="127.0.0.1" NODE_ENV="production" \
  INTERNAL_API_URL="http://127.0.0.1:8000" \
  node apps/web/.next/standalone/apps/web/server.js &
SERVER_PID=$!

for _ in $(seq 1 60); do
  if curl -sf -o /dev/null "${BASE_URL}/login"; then
    break
  fi
  sleep 1
done
if ! curl -sf -o /dev/null "${BASE_URL}/login"; then
  echo "ERRO: servidor standalone não subiu em 60s." >&2
  exit 1
fi

# `--retries=0` sobrescreve o `retries: process.env.CI ? 1 : 0` do
# `playwright.config.ts`. Com retry, uma violação que aparece na 1ª tentativa e
# some na 2ª é classificada como `flaky` e o Playwright sai **0** — gate verde
# com violação `serious` real medida. A11y é determinístico: violação
# intermitente (típica de estado de carregamento) é violação, não ruído.
# `--trace=retain-on-failure` repõe o trace que o config só gerava
# `on-first-retry`.
FINAL_EXIT=0
for THEME in $THEMES; do
  REPORT="test-results/a11y-report-${THEME}.json"
  echo "==> axe-core · tema ${THEME} (reprova em critical/serious)"
  set +e
  E2E_BASE_URL="$BASE_URL" E2E_THEME="$THEME" PLAYWRIGHT_JSON_OUTPUT_NAME="$REPORT" \
    pnpm --filter @auditoria/web exec playwright test "$SPEC" \
      --retries=0 --trace=retain-on-failure --reporter=list,json
  TEST_EXIT=$?
  set -e
  if [[ "$TEST_EXIT" -ne 0 ]]; then
    FINAL_EXIT="$TEST_EXIT"
  fi

  # Rede de segurança contra o modo de falha que motivou este gate: suíte que
  # se auto-pula e devolve "tudo verde" sem ter medido nada — POR TEMA.
  node -e '
    const fs = require("fs");
    const path = "apps/web/'"$REPORT"'";
    if (!fs.existsSync(path)) {
      console.error("ERRO: relatório do Playwright não foi gerado — a suíte não chegou a rodar.");
      process.exit(1);
    }
    const { stats = {} } = JSON.parse(fs.readFileSync(path, "utf8"));
    console.log(`tema '"$THEME"': expected=${stats.expected} unexpected=${stats.unexpected} skipped=${stats.skipped} flaky=${stats.flaky}`);
    if (!(stats.expected > 0)) {
      console.error("ERRO: nenhum teste de a11y passou de fato — verde sem medir nada.");
      process.exit(1);
    }
    if (stats.skipped > 0) {
      console.error(`ERRO: ${stats.skipped} teste(s) de a11y foram pulados — o gate precisa rodar todos.`);
      process.exit(1);
    }
    // Rede do `--retries=0` acima: se o retry voltar (config ou comando), `flaky`
    // volta a existir e o Playwright sai 0 mesmo com violação medida.
    if (stats.flaky > 0) {
      console.error(`ERRO: ${stats.flaky} teste(s) de a11y ficaram FLAKY — violação intermitente conta como violação. Rode sem retry e corrija o spec.`);
      process.exit(1);
    }
  '
done

exit "$FINAL_EXIT"
