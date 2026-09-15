#!/bin/bash
# .claude/hooks/learning-reminder.sh
# Stop — lembra de rodar /retro quando o último commit é um fix sem registrar aprendizado.
# Advisory (não bloqueia): imprime um lembrete. Input: JSON via stdin.

cat >/dev/null  # drena stdin

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
[[ -z "$REPO_ROOT" ]] && exit 0

# Em runs orquestradas (worktree de agent tem CONTEXT.md injetado), o gate do
# orchestrate.js já força o registro — não polui o agent com lembrete.
[[ -f "$REPO_ROOT/CONTEXT.md" ]] && exit 0

SUBJECT=$(git -C "$REPO_ROOT" log -1 --format=%s 2>/dev/null)
# Só lembra após commits de correção (fix...).
echo "$SUBJECT" | grep -qiE '^fix(\(|:|!)' || exit 0

# Se o commit já tocou learnings.md, nada a fazer.
if git -C "$REPO_ROOT" show --name-only --format= HEAD 2>/dev/null | grep -q '\.claude/memory/learnings\.md'; then
  exit 0
fi

echo "💡 Lembrete: o último commit é um fix ('$SUBJECT') e não registrou aprendizado."
echo "   Se corrigiu um erro recorrível, rode /retro para capturar+rotear em learnings.md."
exit 0
