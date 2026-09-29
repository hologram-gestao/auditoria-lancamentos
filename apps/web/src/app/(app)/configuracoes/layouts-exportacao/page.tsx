/**
 * Server wrapper da tela de Layouts de exportação — Sprint 13 (FRONT 13.5).
 *
 * O `<Suspense>` é obrigatório: a tela usa `useSearchParams` (o filtro de
 * organização da plataforma vai na URL) e sem o boundary o Next força a rota
 * inteira a client-side rendering. O guard de RBAC fica no client component; o
 * backend nega com 403 — é ele a autoridade.
 */

import { Suspense } from 'react';

import ExportLayoutsPage from './export-layouts-page';

export default function Page() {
  return (
    <Suspense fallback={<ExportLayoutsFallback />}>
      <ExportLayoutsPage />
    </Suspense>
  );
}

function ExportLayoutsFallback() {
  return (
    <div
      role="status"
      className="space-y-6"
      aria-busy="true"
      aria-label="Carregando layouts de exportação"
    >
      <div className="bg-muted h-7 w-48 animate-pulse rounded" />
      <div className="bg-card h-64 animate-pulse rounded-lg border" />
    </div>
  );
}
