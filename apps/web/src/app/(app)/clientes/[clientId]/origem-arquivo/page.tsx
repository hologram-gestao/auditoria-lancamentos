/**
 * Origem por arquivo do cliente — `/clientes/{clientId}/origem-arquivo`
 * (Sprint 14 / R5).
 *
 * Server component fino: extrai o `clientId` e delega, como `de-para/` e
 * `carteira/`. O `<Suspense>` é obrigatório — a tela usa `useSearchParams`
 * (a competência do envio vem na URL, e é ela que o link do de-para aponta) e
 * sem o boundary o Next força a rota inteira a client-side rendering.
 */

import { Suspense } from 'react';

import { FileOriginScreen } from '@/components/features/file-origin/file-origin-screen';

import FileOriginLoading from './loading';

export default function FileOriginPage({ params }: { params: { clientId: string } }) {
  return (
    <Suspense fallback={<FileOriginLoading />}>
      <FileOriginScreen clientId={params.clientId} />
    </Suspense>
  );
}
