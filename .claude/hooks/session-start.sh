#!/bin/bash
# .claude/hooks/session-start.sh
# SessionStart — mostra o estado da sprint ao iniciar a sessão.

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
[[ -z "$REPO_ROOT" ]] && exit 0

# Estado por projeto (modelo multi-projeto). Em worktrees, sobe até achar .agents-hub.
STATE_FILE="$REPO_ROOT/.agents-hub/sprint-state.json"
echo "=== Sessão iniciada: $(date '+%Y-%m-%d %H:%M:%S') ==="

if [[ -f "$STATE_FILE" ]]; then
  node -e "
    try { const s=JSON.parse(require('fs').readFileSync('$STATE_FILE','utf8'));
      console.log('Sprint ativa :', s.currentSprint||'não definida');
      console.log('Status       :', s.status||'desconhecido');
      if (s.status==='interrupted') console.log('⚠️  Sprint interrompida — veja HANDOFF.md para retomar.');
    } catch(e){ console.log('(estado ilegível)'); }
  " 2>/dev/null || true
else
  echo "Sem sprint ativa. Rode: make sprint-plan / make sprint-start"
fi
exit 0
