#!/bin/bash
# .claude/hooks/no-commit-secrets.sh
# PreToolUse (Bash) — bloqueia commitar segredo.
#
# Por que existe: o repositório é o lugar mais fácil de vazar um segredo e o mais difícil
# de despoluir depois (o valor fica no histórico mesmo após o "fix" que o remove — a
# resposta certa passa a ser ROTACIONAR a credencial, não editar o arquivo). O CI tem um
# scanner, mas ele roda no PR, ou seja, DEPOIS de o segredo já estar empurrado. Este hook
# é o reflexo: barra antes de o commit existir.
#
# Duas checagens, porque as duas falham sozinhas:
#   1. NOME do arquivo  — pega .env, chave privada, service-account.json
#   2. CONTEÚDO staged  — pega o token colado dentro de um .py/.ts/.yml comum
#
# Input: JSON via stdin com tool_input.command.

INPUT=$(cat)
source "$(dirname "${BASH_SOURCE[0]}")/_json.sh"
CMD=$(json_get "$INPUT" tool_input.command)

[[ -z "$CMD" ]] && exit 0
echo "$CMD" | grep -qE 'git +(commit|add)' || exit 0

bloqueia() {
  # A mensagem é escapada: um caminho com aspas quebraria o JSON de saída.
  local motivo="$1"
  local json_motivo
  json_motivo=$(json_escape "$motivo")
  echo "{\"decision\": \"block\", \"reason\": ${json_motivo}}"
  exit 2
}

STAGED=$(git diff --cached --name-only 2>/dev/null)
[[ -z "$STAGED" ]] && exit 0

# ── 1. Nome de arquivo ────────────────────────────────────────────────────────
# .env.example / .env.sample são o CONTRÁRIO de um vazamento (documentam as chaves
# necessárias sem os valores) — precisam passar.
NOME_PROIBIDO='(^|/)\.env($|\.[a-z]+$)|(^|/)\.npmrc$|(^|/)\.netrc$|\.(pem|key|p12|pfx|jks|keystore)$|(^|/)id_(rsa|dsa|ecdsa|ed25519)$|(^|/)(service-account|serviceaccount|gcp-key|credentials)[^/]*\.json$'
NOME_OK='\.env\.(example|sample|template)$|\.env\.example\.'

OFENSORES=$(echo "$STAGED" | grep -E "$NOME_PROIBIDO" | grep -vE "$NOME_OK" || true)
if [[ -n "$OFENSORES" ]]; then
  bloqueia "Bloqueado: arquivo de segredo prestes a ser commitado: $(echo "$OFENSORES" | tr '\n' ' ')
Segredo NUNCA vai para o repositório — nem em branch, nem 'temporariamente' (fica no histórico).
Remova do stage (git restore --staged <arquivo>), garanta a entrada no .gitignore, e use
.env.example para documentar as chaves SEM os valores."
fi

# ── 2. Conteúdo staged ────────────────────────────────────────────────────────
# Padrões de credencial com formato reconhecível — prefixo de provedor ou bloco PEM.
# Deliberadamente NÃO tenta pegar "senha genérica": heurística ampla gera falso positivo
# (toda fixture de teste tem `password = "x"`), falso positivo treina o time a ignorar o
# hook, e hook ignorado não protege nada. O scanner do CI cobre o resto.
#
# ⚠️ Cada alternativa exige o FORMATO da credencial, nunca só o rótulo. A versão anterior
# tinha um `-----BEGIN` solto no fim, e ele custava caro duas vezes:
#   · casava com `-----BEGIN CERTIFICATE-----`, que é material PÚBLICO — falso positivo;
#   · casava com este próprio arquivo, que contém a lista de padrões. Resultado: o hook
#     BLOQUEAVA O PRÓPRIO COMMIT, e não havia como instalá-lo num repo sem `--no-verify`.
# `BEGIN [A-Z ]*PRIVATE KEY` cobre RSA/EC/DSA/OPENSSH/PGP e exige "PRIVATE KEY" de fato.
CONTEUDO_PROIBIDO='BEGIN [A-Z ]*PRIVATE KEY-----|pk_[a-zA-Z0-9]{20,}|sk-ant-[a-zA-Z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|xox[baprs]-[0-9A-Za-z-]{10,}'

ACHADOS=""
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  # Este arquivo é, por definição, uma lista de padrões de credencial. Varrer o próprio
  # conteúdo é garantia de falso positivo — e de um hook que não se deixa instalar.
  [[ "$f" == *"no-commit-secrets.sh" ]] && continue
  # Só o conteúdo STAGED (:0:) — não o working tree, que pode ter algo ainda não commitado.
  if git show ":0:$f" 2>/dev/null | grep -qE "$CONTEUDO_PROIBIDO"; then
    ACHADOS+="$f "
  fi
done <<< "$STAGED"

if [[ -n "$ACHADOS" ]]; then
  bloqueia "Bloqueado: conteúdo com formato de credencial nos arquivos staged: $ACHADOS
Se for credencial real: remova do stage, ROTACIONE a credencial (o valor já esteve em disco)
e mova o valor para o gerenciador de segredos / .env local.
Se for exemplo em teste/doc: use um valor claramente falso (ex.: 'pk_EXEMPLO_NAO_REAL')."
fi

exit 0
