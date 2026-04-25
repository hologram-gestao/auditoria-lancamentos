'use client';

/**
 * Providers globais do app: TanStack Query + Sonner Toaster.
 *
 * Mantém-se em client component à parte para o root layout continuar como server
 * component. Defaults da Query escolhidos para painéis admin (CRUD com mutations
 * frequentes): staleTime curto, retry conservador.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { Toaster } from 'sonner';

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
    <QueryClientProvider client={client}>
      {children}
      <Toaster position="top-right" richColors closeButton />
    </QueryClientProvider>
  );
}
