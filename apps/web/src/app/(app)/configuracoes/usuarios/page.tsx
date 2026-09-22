/**
 * Server wrapper da tela de Usuários — mesmo desenho de `organizacoes/`.
 *
 * O `<Suspense>` é obrigatório: a tela usa `useSearchParams` (a aba ativa vai
 * na URL, 86e3chrxw) e sem o boundary o Next força a rota inteira a
 * client-side rendering. O guard de RBAC fica no client component; o backend
 * nega com 403 em todas as rotas de `/api/v1/users` — é ele a autoridade.
 */

import { Suspense } from 'react';

import UsersPage from './users-page';

export default function Page() {
  return (
    <Suspense fallback={<UsersFallback />}>
      <UsersPage />
    </Suspense>
  );
}

function UsersFallback() {
  return (
    <div role="status" className="space-y-6" aria-busy="true" aria-label="Carregando usuários">
      <div className="bg-muted h-7 w-48 animate-pulse rounded" />
      <div className="bg-card h-64 animate-pulse rounded-lg border" />
    </div>
  );
}
