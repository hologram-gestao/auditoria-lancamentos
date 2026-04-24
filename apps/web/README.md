# apps/web — Frontend Next.js 14

Frontend do Sistema de Auditoria de Lançamentos.

## Setup

A partir da raiz do monorepo:

```bash
pnpm install
cp apps/web/.env.example apps/web/.env.local
pnpm --filter @auditoria/web dev
```

Ou via Makefile: `make dev-web`.

## Estrutura

```
src/
├── app/                 # App Router
│   ├── (auth)/login/    # tela pública (S3)
│   └── (app)/           # rotas autenticadas (S4+)
├── components/
│   ├── ui/              # shadcn/ui
│   └── features/        # componentes de domínio
├── lib/
│   ├── api/             # fetch wrapper + interceptors
│   ├── auth/            # helpers de sessão
│   ├── crypto/          # SHA-256 Web Crypto
│   └── formatters/      # BRL, datas
├── hooks/               # useAuth, etc.
└── stores/              # Zustand slices
```

## Padrões

- **TypeScript strict** + `noUncheckedIndexedAccess`.
- **Server components por padrão.** `"use client"` só quando necessário.
- **Data fetching:** TanStack Query para client components.
- **Forms:** `react-hook-form` + `zod`.
- **Tabelas grandes:** `@tanstack/react-table` + virtualização.

Veja regras completas em [CLAUDE.md](../../CLAUDE.md).
