'use client';

/**
 * Shell de navegação DENTRO de um cliente (Sprint 4 / R6).
 *
 * Antes, "Contas Bancárias" e "Histórico de Conciliações" eram duas seções
 * empilhadas na mesma página. A reunião de 07/07 pediu que virassem dois
 * DESTINOS distintos, com a Lista de Conciliações promovida a tela principal.
 * Este componente é a moldura comum: breadcrumb + nome do cliente + ações.
 *
 * Navegação (86e2n39h7 + 86e2n4pf9): o menu do cliente NÃO mora mais aqui.
 * De `md` para cima ele está no `<aside>` do shell (`SidebarNav`, em camadas);
 * abaixo de `md`, no drawer do hambúrguer (`MobileNavDrawer`) — que renderiza
 * o mesmo `SidebarNav`. Os chips provisórios que viveram aqui entre as duas
 * tasks foram removidos. A árvore é uma só (`features/navigation/nav-items`).
 *
 * O breadcrumb CONTINUA (decisão de 23/08/2026): ele mostra profundidade; o
 * "Voltar para clientes" do sidebar troca de camada. Dentro de uma conciliação
 * (86e2u513w) a trilha ganha o nível dela — derivado do pathname + cache do
 * `useSessionDetail`, nunca registrado pela tela filha — e o nome do cliente
 * vira link: é a volta explícita para a lista. O `aria-current` fica sempre na
 * página realmente atual.
 *
 * Layout (design-system):
 *   - o shell externo (`(app)/layout.tsx`) já é `h-dvh` e só o `<main>` rola;
 *   - o conteúdo é `min-h-0 min-w-0 flex-1`: sem `min-w-0` uma tabela larga
 *     estoura a viewport; sem `min-h-0` (coluna) o item cresce até a altura do
 *     conteúdo e as regiões roláveis internas param de rolar (ADR-007);
 *   - largura total (sem `max-w-*`): listas usam o espaço todo.
 *
 * Carga do cliente: uma única `useClientDetail` no shell alimenta o cache do
 * TanStack — as páginas filhas chamam o mesmo hook e são servidas do cache, sem
 * segundo request.
 */

import { ChevronRight, SquarePen } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';

import { sessionIdFromPathname } from '@/components/features/navigation/nav-items';
import { sessionCrumbLabel } from '@/components/features/reconciliations/session-label';
import { AccessDenied } from '@/components/shared/access-denied';
import { Button } from '@/components/ui/button';
import { useClientDetail } from '@/hooks/use-clients';
import { useSessionDetail } from '@/hooks/use-reconciliations';
import { ApiError } from '@/lib/api/client';
import { canAccessClient, canSeeSystemArea, hasPermission, homePathFor } from '@/lib/authz';
import { useAuthStore } from '@/stores/auth';

import { ClientStatusBadge } from './client-status-badge';
import { EditClientModal } from './edit-client-modal';

interface ClientShellProps {
  clientId: string;
  children: React.ReactNode;
}

export function ClientShell({ clientId, children }: ClientShellProps) {
  const currentUser = useAuthStore((s) => s.user);
  const [editOpen, setEditOpen] = useState(false);

  // Gating de tenant (R4/FRONT 05.7) ANTES do fetch: um usuário de cliente que
  // abre o deep link de OUTRO tenant não deve nem disparar o request — o
  // backend responderia 403/404 e a tela mostraria "não foi possível carregar",
  // que é a mensagem errada (o problema não é técnico, é de permissão).
  // Para usuário `system` isto é `true`: a carteira mora em `client_assignments`
  // e quem nega é o backend — aí sim a tela degrada pela resposta.
  const canAccess = canAccessClient(currentUser, clientId);
  const detailQuery = useClientDetail(clientId, { enabled: canAccess });
  // Nível da SESSÃO no breadcrumb (86e2u513w): derivado 100% do pathname, como
  // a camada do sidebar — a tela filha não registra nada. O hook é o MESMO da
  // tela de detalhe, então o TanStack serve do cache, sem segundo request.
  // Hooks ANTES dos early returns (rules of hooks); `enabled` barra o vazio.
  const pathname = usePathname();
  const sessionId = sessionIdFromPathname(pathname);
  const sessionQuery = useSessionDetail(sessionId ?? '');

  if (currentUser !== null && !canAccess) {
    return (
      <AccessDenied
        message="Este cliente não faz parte do seu acesso. Se você precisa dele, fale com o responsável pela sua conta."
        backHref={homePathFor(currentUser)}
        backLabel="Voltar para o início"
      />
    );
  }

  if (detailQuery.isLoading) {
    return <ClientShellSkeleton />;
  }

  if (detailQuery.isError) {
    const err = detailQuery.error;
    const isNotFound = err instanceof ApiError && err.status === 404;
    return (
      <ClientShellError
        title={isNotFound ? 'Cliente não encontrado' : 'Não foi possível carregar o cliente'}
        message={
          err instanceof ApiError ? err.userMessage : 'Ocorreu um erro inesperado. Tente novamente.'
        }
        onRetry={() => void detailQuery.refetch()}
        showRetry={!isNotFound}
      />
    );
  }

  const client = detailQuery.data;
  if (!client || currentUser === null) return null;

  // §9 é do admin do sistema. Nenhum papel de cliente edita os dados do próprio
  // cliente (credenciais Omie moram aí) — e o gerente do sistema também não.
  const canEditClient = hasPermission(currentUser, 'edit_client');
  // O elo "Clientes" do breadcrumb aponta para a lista GLOBAL. Para usuário de
  // tenant esse destino é negado: o breadcrumb começa no próprio cliente.
  const showClientsCrumb = canSeeSystemArea(currentUser);
  // Dentro de uma conciliação a trilha ganha o nível dela e o cliente vira
  // LINK (a volta explícita para a lista). Erro na carga da sessão não some
  // com o nível: rótulo genérico mantém o caminho de volta visível — quem
  // explica o erro é a tela filha.
  const inSession = sessionId !== null;
  let sessionCrumb: string | undefined;
  if (inSession) {
    if (sessionQuery.data !== undefined) {
      sessionCrumb = sessionCrumbLabel(sessionQuery.data, client.accounts ?? []);
    } else if (sessionQuery.isError) {
      sessionCrumb = 'Conciliação';
    }
  }

  return (
    <div className="flex h-full flex-col gap-6">
      <header className="space-y-3">
        <nav aria-label="Breadcrumb" className="text-muted-foreground text-sm">
          <ol className="flex items-center gap-1.5">
            {showClientsCrumb && (
              <>
                <li>
                  <Link href="/clientes" className="hover:text-foreground hover:underline">
                    Clientes
                  </Link>
                </li>
                <li aria-hidden="true">
                  <ChevronRight className="h-3.5 w-3.5" />
                </li>
              </>
            )}
            {inSession ? (
              <>
                <li className="min-w-0">
                  <Link
                    href={`/clientes/${clientId}`}
                    className="hover:text-foreground block truncate hover:underline"
                  >
                    {client.name}
                  </Link>
                </li>
                <li aria-hidden="true">
                  <ChevronRight className="h-3.5 w-3.5" />
                </li>
                <li className="text-foreground truncate font-medium" aria-current="page">
                  {sessionCrumb ?? (
                    <>
                      <span
                        className="bg-muted inline-block h-3 w-28 animate-pulse rounded"
                        aria-hidden="true"
                      />
                      <span className="sr-only">Carregando conciliação…</span>
                    </>
                  )}
                </li>
              </>
            ) : (
              <li className="text-foreground truncate font-medium" aria-current="page">
                {client.name}
              </li>
            )}
          </ol>
        </nav>

        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold">{client.name}</h1>
            <ClientStatusBadge active={client.active} />
          </div>
          {canEditClient && (
            <Button variant="outline" onClick={() => setEditOpen(true)}>
              <SquarePen className="h-4 w-4" aria-hidden="true" />
              Editar cliente
            </Button>
          )}
        </div>
      </header>

      {/* `min-h-0` (ADR-007): sem ele o item flex cresce até a altura do
          conteúdo e as regiões internas (TableCard/ScrollRegion) nunca rolam —
          a barra de paginação voltaria a cobrir linhas (86e2u4nxg/86e2uca1d,
          pego pelo gate quando o layout virou coluna). */}
      <div className="min-h-0 min-w-0 flex-1">{children}</div>

      <EditClientModal open={editOpen} onOpenChange={setEditOpen} client={client} />
    </div>
  );
}

function ClientShellSkeleton() {
  return (
    <div role="status" className="space-y-6" aria-busy="true" aria-label="Carregando cliente">
      <div className="space-y-3">
        <div className="bg-muted h-3 w-32 animate-pulse rounded" />
        <div className="flex items-center gap-3">
          <div className="bg-muted h-7 w-64 animate-pulse rounded" />
          <div className="bg-muted h-5 w-16 animate-pulse rounded-full" />
        </div>
      </div>
      <div className="flex flex-col gap-6">
        <div className="flex-1 space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="bg-card space-y-3 rounded-lg border p-4 shadow-sm">
              <div className="bg-muted h-4 w-1/3 animate-pulse rounded" />
              <div className="bg-muted h-3 w-2/3 animate-pulse rounded" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

interface ClientShellErrorProps {
  title: string;
  message: string;
  onRetry: () => void;
  showRetry: boolean;
}

function ClientShellError({ title, message, onRetry, showRetry }: ClientShellErrorProps) {
  return (
    <div className="space-y-4">
      <nav aria-label="Breadcrumb" className="text-muted-foreground text-sm">
        <Link href="/clientes" className="hover:text-foreground hover:underline">
          ← Voltar para clientes
        </Link>
      </nav>
      <div
        role="alert"
        className="bg-destructive/5 border-destructive/30 text-destructive space-y-3 rounded-lg border p-6"
      >
        <h1 className="text-lg font-semibold">{title}</h1>
        <p className="text-sm">{message}</p>
        {showRetry && (
          <Button variant="outline" size="sm" onClick={onRetry}>
            Tentar novamente
          </Button>
        )}
      </div>
    </div>
  );
}
