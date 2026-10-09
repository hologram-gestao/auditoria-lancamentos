/**
 * Server wrapper da tela "Destinos do de-para" (86e3n70pn) — mesmo desenho de
 * `usuarios/`.
 *
 * O `<Suspense>` é obrigatório: a tela usa `useSearchParams` (destino, filtros e
 * página na URL; o de-para do cliente manda para cá com `?destino=<id>`) e sem o
 * boundary o Next força a rota inteira a client-side rendering. O guard de RBAC
 * fica no client component; o servidor nega com 403 toda escrita no catálogo.
 */

import { Suspense } from 'react';

import { MappingCatalogScreen } from '@/components/features/mapping-catalog/mapping-catalog-screen';

export default function Page() {
  return (
    <Suspense fallback={<MappingCatalogFallback />}>
      <MappingCatalogScreen />
    </Suspense>
  );
}

function MappingCatalogFallback() {
  return (
    <div
      role="status"
      className="space-y-6"
      aria-busy="true"
      aria-label="Carregando os destinos do de-para"
    >
      <div className="bg-muted h-7 w-48 animate-pulse rounded" />
      <div className="bg-card h-64 animate-pulse rounded-lg border" />
    </div>
  );
}
