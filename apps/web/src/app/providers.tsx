'use client';

/**
 * Providers globais do app: ThemeProvider (next-themes) + TanStack Query +
 * Sonner Toaster.
 *
 * Mantém-se em client component à parte para o root layout continuar como server
 * component. Defaults da Query escolhidos para painéis admin (CRUD com mutations
 * frequentes): staleTime curto, retry conservador.
 *
 * Tema (86e2n39hb, decisões de 25/08/2026): padrão `system`, persistência no
 * localStorage (os dois defaults do next-themes). O provider vive AQUI — layout
 * RAIZ, não no shell autenticado — porque o tema vale também no login (decisão
 * (c) da task). `attribute="class"` casa com o `darkMode: ['class']` do
 * Tailwind; `disableTransitionOnChange` evita o piscar de cores na troca; o
 * script inline do próprio next-themes aplica a classe ANTES da hidratação
 * (sem FOUC — exige o `suppressHydrationWarning` que o `<html>` já tem).
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider, useTheme } from 'next-themes';
import { useState } from 'react';
import { Toaster } from 'sonner';

/**
 * Cores do toast pelos TOKENS do tema, não pela paleta `richColors` do Sonner.
 *
 * **Por que `richColors` saiu** (defeito medido, não preferência): o verde dele
 * no tema claro é `--success-text: hsl(140,100%,27%)` sobre
 * `--success-bg: hsl(143,85%,96%)` → o axe em Chromium real mediu `#008a2e`
 * sobre `#ecfdf3` = **4.25:1**, abaixo do AA de 4.5:1 para os 13px do
 * `[data-title]`. Reprovou o job `web_a11y` em `[desktop]` e `[mobile]`.
 *
 * **Por que a correção é aqui e não no call site:** o call site só escolhe o
 * TIPO (`toast.success`/`toast.error`); quem pinta é o `<Toaster>`. Corrigir em
 * cada `toast.*` seria a cor hardcoded que o design-system proíbe, em ~20
 * lugares.
 *
 * **Por que dá para sobrescrever com classe do Tailwind:** a regra base do
 * Sonner é `:where([data-sonner-toast][data-styled="true"])` — `:where()` tem
 * especificidade ZERO, então qualquer utilitário ganha. Já as regras de
 * `richColors` são `[data-rich-colors=true][data-sonner-toast][data-type=…]`
 * (0,3,0) e ganhariam da classe — daí a prop ter de sair, não bastar
 * acrescentar `classNames` (conferido em `node_modules/sonner/dist/styles.css`,
 * não presumido).
 *
 * **Um par por TIPO, nada de base comum:** o Sonner concatena
 * `classNames.default` **e** `classNames[tipo]` no mesmo elemento (conferido em
 * `dist/index.mjs`), então pôr fundo/texto nos dois deixaria o vencedor por
 * conta da ordem em que o Tailwind emite os utilitários. Só o tipo pinta; o
 * toast neutro (`toast(...)`) fica com o `--normal-bg`/`--normal-text` do
 * Sonner (branco sobre quase-preto, ~18:1).
 *
 * Os pares abaixo são os mesmos dos badges e estão travados contra regressão em
 * `src/app/__tests__/theme-contrast.test.ts` (claro e escuro).
 */
const TOAST_CLASSNAMES = {
  success: 'bg-success-muted text-success border-success/30',
  error: 'bg-destructive-muted text-destructive border-destructive/30',
  warning: 'bg-warning-muted text-warning border-warning/30',
  info: 'bg-info-muted text-info border-info/30',
} as const;

/**
 * O toast TIPADO pinta pelos tokens acima (flipam sozinhos com a classe
 * `.dark`); o toast NEUTRO usa o `--normal-bg` do próprio Sonner, que só muda
 * com a prop `theme` — sem ela ele ficaria branco no tema escuro. Componente à
 * parte porque `useTheme` precisa estar DENTRO do ThemeProvider.
 */
function AppToaster() {
  const { resolvedTheme } = useTheme();
  // Hologram é um tema de superfícies ESCURAS — para o toast neutro do Sonner
  // ele conta como dark; os tipados pintam pelos tokens e flipam sozinhos.
  return (
    <Toaster
      position="top-right"
      closeButton
      theme={resolvedTheme === 'light' ? 'light' : 'dark'}
      toastOptions={{ classNames: TOAST_CLASSNAMES }}
    />
  );
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            gcTime: 5 * 60_000,
            refetchOnWindowFocus: false,
            retry: 1,
          },
          mutations: {
            retry: 0,
          },
        },
      }),
  );

  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
      // O terceiro tema (86e2ukrc9): o `system` continua resolvendo só
      // claro/escuro — Hologram é escolha manual no toggle.
      themes={['light', 'dark', 'hologram']}
    >
      <QueryClientProvider client={client}>
        {children}
        <AppToaster />
      </QueryClientProvider>
    </ThemeProvider>
  );
}
