# E2E / a11y (Playwright)

Suíte que roda contra a aplicação **de verdade** — é onde o DoD de
acessibilidade da Sprint 4 é verificado num browser (contraste, foco e ordem de
leitura não existem em jsdom).

> A checagem de a11y que roda em TODA esteira (`pnpm test`) é a de componente,
> em jsdom, via `src/test/a11y.ts`. Esta aqui é complementar e exige ambiente.

## Pré-requisitos

```bash
# 1) infra + API + seed (na raiz do monorepo)
pnpm infra:up && pnpm db:migrate && pnpm db:seed
pnpm dev:api

# 2) web
pnpm dev:web

# 3) browser do Playwright (uma vez; baixa ~150 MB)
pnpm --filter @auditoria/web exec playwright install chromium
```

## Variáveis

| Variável         | Default                 | Para quê                                            |
| ---------------- | ----------------------- | --------------------------------------------------- |
| `E2E_BASE_URL`   | `http://localhost:3000` | origem do Next                                       |
| `E2E_EMAIL`      | admin do seed           | login                                                |
| `E2E_PASSWORD`   | —                       | **obrigatório**; sem ele os testes são `skip`        |
| `E2E_CLIENT_ID`  | —                       | **obrigatório**; UUID de cliente com ≥ 1 conciliação |

Sem `E2E_PASSWORD`/`E2E_CLIENT_ID` a suíte faz `test.skip` em vez de falhar —
o objetivo é não quebrar quem roda `pnpm e2e` sem ambiente montado.

## Rodar

```bash
E2E_PASSWORD='...' E2E_CLIENT_ID='...' pnpm --filter @auditoria/web e2e
```
