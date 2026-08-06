/**
 * Glossário do cliente — `/clientes/{clientId}/glossario` (Sprint 6 / R2).
 *
 * Server component fino: extrai o `clientId` e delega. O `<Suspense>` é
 * obrigatório — a tela usa `useSearchParams` (paginação na URL) e sem o
 * boundary o Next força a rota inteira a client-side rendering.
 */

import { Suspense } from 'react';

import { GlossaryScreen } from '@/components/features/glossary/glossary-screen';

import GlossaryLoading from './loading';

export default function GlossaryPage({ params }: { params: { clientId: string } }) {
  return (
    <Suspense fallback={<GlossaryLoading />}>
      <GlossaryScreen clientId={params.clientId} />
    </Suspense>
  );
}
