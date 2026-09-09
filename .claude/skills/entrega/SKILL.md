---
name: entrega
description: >
  Feche QUALQUER task de código do jeito da casa: gate rodado, commit local no
  formato certo, NADA publicado, comandos de push/PR entregues prontos em PT-BR.
  Gatilhos literais: "terminei", "pode commitar", "fecha a task", "prepara o PR",
  "manda pro develop" — o fim de toda task de código passa por aqui.
---

# /entrega — fechar a task sem publicar

O roteiro de encerramento que antes vivia só na memória de quem já entregou. Vale
para o dev, para esta sessão e para os agents do `.agents-hub` (cuja memória é
apagada a cada run).

## 0. Pré-requisito que nasceu no INÍCIO da task: a branch

- Branch nasce de `develop`, **nunca** de `main`; commit direto na `main` é
  proibido. Fluxo: branch ← develop → PR para develop → merge → teste → PR
  develop → main.
- Antes de criar a branch, develop tem que estar sincronizada com a main:

```bash
git fetch origin --prune && git rev-list --left-right --count origin/develop...origin/main
```

  Esperado `0	0` (ou develop à frente). `0	N` = develop atrás → fast-forward
  antes de ramificar.
- **Uma branch e uma task por execução.** O tempo é contado da criação da branch
  ao último commit — abrir a branch tarde subestima o registro. Verificável:

```bash
git log --oneline develop..HEAD   # só commits DESTA task
```

## 1. Rode a skill `gate`

Invoque a skill **`gate`** (`.claude/skills/gate/SKILL.md`) — os comandos de teste
moram **lá**, não aqui; não os recopie. Saída aceitável: verde completo com os
números citados, ou **"gate PARCIAL"** com o motivo declarado (ex.: Docker off).
"Deve passar" não fecha task (CLAUDE.md §6.10).

## 2. Commit local

- **Conventional Commits, mensagem inteira em EN-US** (corpo e rodapé inclusive) —
  o formato não muda, só o idioma (CLAUDE.md §7). Rodapé `Co-Authored-By` quando o
  Claude participou do código.
- Os hooks (husky + lint-staged + commitlint) rodam no commit. **`--no-verify` é
  proibido**: hook falhando local = CI falhando igual; conserte antes.
- Arquivo novo dentro de `.claude/` precisa de `git add -f` (o `.gitignore` da
  raiz ignora `.claude/` inteiro; precedente: `decisions.md`, `learnings.md`, as
  skills). O lint-staged imprime `[FAILED]` cosmético ao re-adicionar esses paths —
  o commit passa; confirme com `git log -1 --oneline`.

## 3. NÃO publique — regra dura

Nada de `git push`, nada de `gh pr create`. O deny do `.claude/settings.json`
(`"Bash(git push *)"`) bloqueia por configuração, **de propósito** — não tente
contornar por wrapper, alias ou sandbox. Esta skill existe para tornar o bloqueio
produtivo: o dev publica quando quiser, com os comandos do passo 4.

## 4. Entregue os DOIS comandos prontos

```bash
git push -u origin <branch>
gh pr create --base develop --title "<título em PT-BR>" --body "<corpo COMPLETO em PT-BR>"
```

- O corpo do PR **já vai escrito, em PT-BR** — nunca placeholder: o que muda, como
  foi validado (números reais do gate), pendências e achados. PR gerado com Claude
  termina com `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Lembrete de expectativa: **PR para develop não roda CI** (ver skill `gate`, §4) —
  "no checks reported" é normal; a evidência é o gate local citado no corpo.

## 5. Gatilho do CLAUDE.md §13

Pergunte explicitamente: esta entrega mudou decisão arquitetural, regra de negócio
crítica, padrão que vale para o projeto inteiro, ou contradiz a documentação?

- **Sim** → o `CLAUDE.md` muda **nesta mesma entrega** (mesmo commit/PR), com o
  rodapé de versão ajustado.
- **Não** → a resposta final **diz** "verificado §13: primer não muda" — silêncio
  não conta como verificação.

## 6. Resposta final no formato §12

Duas partes, nesta ordem:

1. **Resumo executivo** — arquivos novos/modificados, tamanho do diff
   (`git diff --stat develop..HEAD`), hash do commit (`git log -1 --format=%h`),
   status do gate com os números reais.
2. **Passo a passo de teste manual** — comandos exatos (assumir Windows + Git
   Bash), estado esperado em cada passo, caminho feliz **e pelo menos um caminho
   de erro** relevante (RBAC, 409, validação, tenant errado…). Se algo não pode
   ser testado agora, dizer o porquê e quando será coberto.

## Checklist de saída

- [ ] `git log --oneline develop..HEAD` mostra só commits desta task
- [ ] Gate citado com números (ou PARCIAL declarado com motivo)
- [ ] `git status --short` sem resíduo da task
- [ ] Zero push / zero PR; os 2 comandos entregues com corpo em PT-BR
- [ ] §13 verificado e **reportado**
- [ ] Resposta final nas duas partes do §12
