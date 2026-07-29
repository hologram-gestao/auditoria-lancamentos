/**
 * Layout de todas as rotas de um cliente — Sprint 4 / R6.
 *
 * Server component: só extrai o `clientId` e delega a moldura (breadcrumb,
 * cabeçalho, navegação Conciliações / Contas Bancárias) para o `<ClientShell>`,
 * mantendo a regra "server components por padrão" (CLAUDE.md §7).
 */

import { ClientShell } from '@/components/features/clients/client-shell';

export default function ClientLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: { clientId: string };
}) {
  return <ClientShell clientId={params.clientId}>{children}</ClientShell>;
}
