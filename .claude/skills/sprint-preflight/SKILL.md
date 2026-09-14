---
name: sprint-preflight
description: >
  Checklist OBRIGATÓRIO antes de disparar uma sprint do agents-hub, e ao retomar uma
  interrompida. Gatilhos literais: "rodar sprint", "sprint-start", "sprint-plan",
  "agents-up", "orquestrar", "agents-hub", "retomar sprint", "make sprints". Rodar uma
  sprint multi-agente custa dinheiro e horas, e todas as falhas conhecidas são
  detectáveis ANTES do start — inclusive as duas que criam trabalho invisível: sprint
  já feita sendo replanejada, e deliverable que o commit do orquestrador não leva.
---

# /sprint-preflight — pré-voo antes de rodar uma sprint do agents-hub

O hub é um repositório separado (`~/agents-hub`) que opera sobre este projeto por
worktrees em `.worktrees/agent-*`. O conhecimento abaixo não sobrevive dentro dos
agents: `.claude/memory/*`, o `CLAUDE.md` do papel, `PROJECT.md` e `CONTEXT.md` são
**re-semeados a cada run** — só o `HANDOFF.md` e o ClickUp atravessam. Por isso o
pré-voo é do operador, aqui, antes do start.

Tudo abaixo foi rodado em 14/09/2026. Se um output não bater, o ambiente mudou:
releia, não assuma.

## Passo 0 — `make doctor` primeiro, e saiba ler a saída

Os targets do hub rodam **da raiz deste projeto** por um `GNUmakefile` local e
não-rastreado, que injeta `REPO_ROOT=$(CURDIR)` (decisão do Pedro: ferramenta de
execução nunca entra em commit). Se ele sumir, os targets somem junto — recriar é
`make -C ~/agents-hub <target> REPO_ROOT=$(pwd)`.

```bash
make doctor
```

Ele já valida built-in: git, Claude CLI, `gh` autenticado, repo com remoto e branch
base, `.claude/agents/*.md`, token e IDs do ClickUp, statuses obrigatórios na lista e
a página da sprint no doc. **O exit code não é o veredito** — na medição de 14/09 ele
saiu diferente de zero por "gcloud não autenticado", que é pré-requisito de DEPLOY e
não de sprint. Leia item a item e decida o que bloqueia:

| O que apareceu                                         | Bloqueia a sprint?                                                                                |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| `gcloud não autenticado`                               | Não. Só o deploy precisa.                                                                         |
| `hook local do trunk não está ligado`                  | Não, mas rode `make protect-trunk` — é a trava contra push direto na main.                        |
| `core.hooksPath aponta p/ .husky/_`                    | Não: o husky é do projeto. A trava do trunk precisa ser chamada de dentro do `.husky/_/pre-push`. |
| `O template do hub mudou desde o seu init`             | Não, mas veja o diff antes de rodar: pode conter correção do próprio orquestrador.                |
| token/IDs do ClickUp, agents ausentes, repo sem remoto | **Sim.**                                                                                          |

O aviso de template compara `.agents-hub/.template-rev` com o hub; em 14/09 o local
estava em `f5a8e86` e o hub em `1b3e474`. Ver o que mudou, sem aplicar nada:

```bash
git -C ~/agents-hub diff f5a8e86..1b3e474 -- templates/   # troque pelos hashes que o doctor imprimir
```

Aplicar é `make hub-init`, **nunca `make init`**: o target do projeto restaura os
arquivos rastreados que o init do hub sobrescreve. Foi assim que a delegação se perdeu
num stash em 14/07/2026 e ficou um mês fora do ar.

## Passo 1 — `develop` está igual à `main`?

A branch base é `develop` (`BASE_BRANCH`, default em `orchestrate.js:48`; o
`config.env` do projeto deixa a chave comentada). Sprint que nasce de uma `develop`
atrasada gera conflito no merge final, depois de todo o trabalho feito.

```bash
git fetch --prune
git diff origin/develop origin/main --stat   # esperado: VAZIO (mesmo conteúdo)
git rev-list --left-right --count origin/develop...origin/main
```

⚠️ **O teste é o CONTEÚDO, não a contagem de commits.** Logo depois de promover
develop para main, o `rev-list` devolve `0	1` e isso é normal: o commit a mais é o
próprio merge da promoção, e o `git diff` sai vazio (medido em 14/09). Só há trabalho a
fazer quando o `diff` mostra arquivos — aí sincronize antes de ramificar (`make
base-sync`, ou `merge --ff-only origin/main`). Contar commits assustaria à toa em todo
pré-voo feito depois de uma promoção.

## Passo 2 — Docker ligado, e peça AGORA

Testcontainers, Postgres local, `pnpm infra:up` e Playwright dependem dele. Peça para
ligar **no início**, não quando o comando falhar no meio da sprint.

```bash
docker ps   # desligado aparece como "could not be found in this WSL 2 distro"
```

## Passo 3 — `package.json` da raiz, nas DUAS pontas

O hub **sobrescreve** o `package.json` da raiz do worktree pelo dele (`"name":
"agents-hub"`, deps `axios`/`dotenv`) porque o `clickup-bridge.js` precisa. Efeito:
`pnpm install --frozen-lockfile` aborta com `ERR_PNPM_OUTDATED_LOCKFILE` e **o gate
inteiro do front deixa de rodar** — lint, type-check, test e build, todos silenciosos.

Antes e depois do run:

```bash
head -2 package.json                      # esperado: "name": "auditoria-lancamentos"
git status --short package.json           # esperado: vazio
git checkout -- package.json              # conserto, se veio trocado
```

Nunca use `pnpm install --no-frozen-lockfile` como atalho: isso reescreve o
`pnpm-lock.yaml` versionado a partir do `package.json` errado. O `node_modules/` da
raiz sobrevive ao conserto, então o bridge continua funcionando.

## Passo 4 — Qual sprint o hub acha que é a próxima (há DUAS fontes, e uma mente)

O hub considera uma sprint feita por duas vias: um `✅` em qualquer posição do título
da página no doc do ClickUp, ou o nome em `state.completedSprints`. As duas podem
falhar ao mesmo tempo, porque quem escreve o `✅` é best-effort e só emite warning.

```bash
make sprints                      # a lista do DOC — é esta que manda
cat .agents-hub/sprint-state.json # o estado LOCAL
```

Medido em 14/09: o `make sprints` mostra as **8 sprints com ✅ e nenhuma marcada
`▶️ próxima`**, enquanto o `sprint-state.json` local ainda diz `"Sprint 3 …
interrupted"` com `completedSprints: []`. **As duas divergem, e a certa é o doc.** Sem
uma sprint pendente, planejar é sobre uma sprint nova no doc, não sobre a fila atual.

⚠️ **`make sprint-plan` CRIA TASKS no ClickUp**, e o `--plan` sai **antes** do
preflight completo — nem o gate de validação humana nem a checagem de worktree
protegem. Se a sprint errada estiver marcada como próxima, você cria tasks duplicadas
e só descobre depois. Confirme a seta com `make sprints` antes; se estiver errada,
corrija o título no doc em vez de conviver com `SPRINT=<n>`, senão o problema volta.
Para só olhar: `make sprint-plan-dry` (preview, não cria nada).

## Passo 5 — Estado da sprint e `HANDOFF.md`

O hook de sessão (`.claude/hooks/session-start.sh`) já imprime a sprint ativa e avisa
quando o status é `interrupted`. Retomada começa lendo o handoff do papel, que é o
único arquivo de memória que sobrevive ao re-seed:

```bash
ls -la .worktrees/*/HANDOFF.md
git worktree list       # em 14/09: 6 linhas — o repo principal + 5 worktrees de agente, todos em sprint-07/* (sobra da Sprint 7)
```

Worktree de sprint encerrada que ficou para trás não quebra nada sozinho, mas confunde
o diagnóstico. Limpar tudo é `make agents-reset` — que **apaga worktrees, branches e
logs**, então só depois de confirmar que nada dali falta ser mergeado.

## Passo 6 — Os `gitPaths` de cada papel cobrem o que o papel escreve?

`git add <caminho>` que não casa com nada é **no-op silencioso**: o agent trabalha, o
orquestrador "commita", e o deliverable não entra — sem uma linha de erro. O hub tem
diagnóstico próprio para isso (`diagnosticarGitPaths`, com teste), mas a conferência
custa dez segundos:

```bash
grep -n "AGENT_PATHS" .agents-hub/config.env
```

Em 14/09: backend `apps/api/`, frontend `apps/web/`, QA `apps/api/tests/ apps/web/e2e/
apps/web/src/`, infra `docker/ .github/ scripts/ .env.example` e os dois
`.env.example` dos apps. ⚠️ **A raiz do monorepo não é de papel nenhum**: `.gitignore`,
`package.json` e `CLAUDE.md` da raiz ficam fora de todos os `gitPaths`, então uma
instrução de sprint que peça para editá-los produz trabalho que o commit não leva.
Edite você mesmo, ou verifique depois.

## Passo 7 — Uma task, uma branch, um run

Task no ClickUp por execução, atribuída ao dev, com branch nova. O tempo conta da
criação da branch ao último commit, então abrir a branch tarde subestima o registro.
Deploy é task separada. Nunca comentar, editar ou linkar task criada por outra pessoa.

## Passo 8 — O `cd` vaza entre comandos

Ler task pelo `clickup-bridge` exige estar no diretório do hub, e o `git` seguinte vai
para o repositório errado sem avisar. **Prefixe sempre**:

```bash
git -C /home/phaos93/auditoria-lancamentos status
node ~/agents-hub/clickup-bridge.js get-task <ID>
```

## Passo 9 — O que MUDOU desde que este checklist foi escrito (não repita o alerta velho)

- **`get-last-qa-comment` foi CORRIGIDO.** A versão antiga devolvia a reprovação mais
  ANTIGA (havia um `.reverse()` antes do `.find()`), e o agent reprovado duas vezes
  corrigia o defeito da rodada anterior. Hoje a função ordena por `date` e devolve a
  mais recente, travado por teste no hub. **A lição residual continua valendo**: se o
  comentário descreve um defeito que o `HANDOFF.md` já registra como corrigido, não
  re-corrija — procure a task de follow-up com a instrução atual.
- **`AGENT_PATHS_QA` já inclui `apps/web/e2e/`.** O hub cita o ADL como o caso onde
  esse caminho faltava; no `config.env` atual ele está lá.

```bash
node --test ~/agents-hub/test/orchestrate.test.js 2>&1 | tail -3   # a suíte do hub, se quiser confirmar
```

## Pós-run — antes de considerar a sprint entregue

1. `package.json` da raiz de volta ao normal (passo 3), nas duas pontas.
2. `git -C <repo> diff --name-only <base>..HEAD` — o que a sprint realmente commitou
   bate com o que as tasks pediam? Arquivo de raiz não entra (passo 6).
3. O `✅` chegou ao título da página no doc? Se não, `make sprints` vai oferecer a
   sprint feita como próxima na rodada seguinte — corrija o título.
4. Memória e `CLAUDE.md` atualizados na mesma entrega; o que precisa sobreviver vai
   para o `HANDOFF.md` ou para uma task, nunca para `.claude/memory/` do worktree.

## Fechamento

Feche com a skill `entrega`. O gate de qualidade do que a sprint produziu é a skill
`gate` — pré-voo não substitui portão de saída.
