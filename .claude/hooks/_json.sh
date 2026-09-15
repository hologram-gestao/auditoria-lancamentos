#!/bin/bash
# .claude/hooks/_json.sh — leitura do payload dos hooks SEM depender de `jq`.
#
# Por que existe: todos os hooks liam o JSON de stdin com
#   CAMPO=$(echo "$INPUT" | jq -r '.x.y // empty' 2>/dev/null)
#   [[ -z "$CAMPO" ]] && exit 0
# e `jq` NÃO está instalado nesta máquina. O `2>/dev/null` engolia o erro, a variável
# saía vazia e o hook fazia `exit 0` — ou seja, TODO hook falhava ABERTO, em silêncio.
# O `no-commit-secrets.sh`, que existe para barrar segredo antes do commit, estava
# inerte exatamente por isso. Um bloqueio que não bloqueia é pior que nenhum: dá a
# sensação de proteção sem a proteção.
#
# `python3` é a escolha em vez de `jq` por estar garantido (o backend é Python 3.12 e
# o binário mora em /usr/bin), enquanto `jq` é dependência externa que ninguém instala.
#
# Uso:  source "$(dirname "${BASH_SOURCE[0]}")/_json.sh"
#       CMD=$(json_get "$INPUT" tool_input.command)
#       AGENTE=$(json_get "$INPUT" agent_type desconhecido)

# json_get <json> <campo.pontilhado> [default]
# Escreve o valor em stdout, ou o default (vazio se omitido) quando o caminho não
# existe. Nunca falha: JSON inválido devolve o default.
json_get() {
  local entrada="$1" campo="$2" padrao="${3-}"
  printf '%s' "$entrada" | python3 -c '
import sys, json
campo, padrao = sys.argv[1], sys.argv[2]
try:
    dado = json.load(sys.stdin)
except Exception:
    print(padrao, end=""); sys.exit(0)
for parte in campo.split("."):
    dado = dado.get(parte) if isinstance(dado, dict) else None
    if dado is None:
        break
if dado is None or dado == "":
    print(padrao, end="")
elif isinstance(dado, str):
    print(dado, end="")
else:
    print(json.dumps(dado), end="")
' "$campo" "$padrao" 2>/dev/null
}

# json_escape <texto> — devolve o texto como string JSON (com aspas), para montar a
# resposta `{"decision":"block","reason":...}` sem quebrar em aspas ou quebra de linha.
json_escape() {
  printf '%s' "$1" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()), end="")' 2>/dev/null
}
