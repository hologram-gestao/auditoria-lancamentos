'use client';

/**
 * Shell de navegação DENTRO de um cliente (Sprint 4 / R6).
 *
 * Antes, "Contas Bancárias" e "Histórico de Conciliações" eram duas seções
 * empilhadas na mesma página. A reunião de 07/07 pediu que virassem dois
 * DESTINOS distintos, com a Lista de Conciliações promovida a tela principal.
 * Este componente é a moldura comum: breadcrumb + nome do cliente + ações, e a
 * barra lateral com os itens de navegação.
 *
 * Layout (design-system):
 *   - o shell externo (`(app)/layout.tsx`) já é `h-dvh` e só o `<main>` rola;
 *   - aqui a coluna de navegação do cliente é `shrink-0` e o conteúdo `min-w-0`,
 *     senão uma tabela larga empurraria a nav para fora da viewport;
 *   - largura total (sem `max-w-*`): listas usam o espaço todo.
 *
 * Carga do cliente: uma única `useClientDetail` no shell alimenta o cache do
 * TanStack — as páginas filhas chamam o mesmo hook e são servidas do cache, sem
 * segundo request.
 */

import {
  ChevronRight,
  Landmark,
  LayoutDashboard,
  ListChecks,
  SquarePen,
  UserCog,
} from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';

import { AccessDenied } from '@/components/shared/access-denied';
import { Button } from '@/components/ui/button';
import { useClientDetail } from '@/hooks/use-clients';
import { ApiError } from '@/lib/api/client';
import { canAccessClient, canSeeSystemArea, hasPermission, homePathFor } from '@/lib/authz';
import { cn } from '@/lib/utils';
import { useAuthStore } from '@/stores/auth';

import { ClientStatusBadge } from './client-status-badge';
import { EditClientModal } from './edit-client-modal';

interface ClientShellProps {
  clientId: string;
  children: React.ReactNode;
}

export function ClientShell({ clientId, children }: ClientShellProps) {
  const currentUser = useAuthStore((s) => s.user);
  const pathname = usePathname();
  const [editOpen, setEditOpen] = useState(false);

  // Gating de tenant (R4/FRONT 05.7) ANTES do fetch: um usuário de cliente que
  // abre o deep link de OUTRO tenant não deve nem disparar o request — o
  // backend responderia 403/404 e a tela mostraria "não foi possível carregar",
  // que é a mensagem errada (o problema não é técnico, é de permissão).
  // Para usuário `system` isto é `true`: a carteira mora em `client_assignments`
  // e quem nega é o backend — aí sim a tela degrada pela resposta.
  const canAccess = canAccessClient(currentUser, clientId);
  const detailQuery = useClientDetail(clientId, { enabled: canAccess });

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

  const base = `/clientes/${clientId}`;
  const accountsHref = `${base}/contas`;
  const dashboardHref = `${base}/painel`;
  const usersHref = `${base}/usuarios`;
  // "Conciliações" continua ativo dentro do detalhe de uma conciliação — é a
  // mesma área de navegação, só que um nível abaixo.
  const isAccounts = pathname.startsWith(accountsHref);
  const isDashboard = pathname.startsWith(dashboardHref);
  const isUsers = pathname.startsWith(usersHref);
  const isReconciliations = !isAccounts && !isDashboard && !isUsers;

  // Matriz do R4 (`lib/authz`), nunca `role === '...'` solto: "Usuários" é do
  // gerente do cliente e do admin do sistema. O gerente do sistema opera a
  // carteira, mas não administra as pessoas de dentro do tenant.
  const canManageClientUsers = hasPermission(currentUser, 'manage_client_users');
  // §9 é do admin do sistema. Nenhum papel de cliente edita os dados do próprio
  // cliente (credenciais Omie moram aí) — e o gerente do sistema também não.
  const canEditClient = hasPermission(currentUser, 'edit_client');
  // O elo "Clientes" do breadcrumb aponta para a lista GLOBAL. Para usuário de
  // tenant esse destino é negado: o breadcrumb começa no próprio cliente.
  const showClientsCrumb = canSeeSystemArea(currentUser);

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
            <li className="text-foreground truncate font-medium" aria-current="page">
              {client.name}
            </li>
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

      <div className="flex min-h-0 flex-1 flex-col gap-6 lg:flex-row">
        <nav aria-label="Seções do cliente" className="shrink-0 lg:w-52">
          {/* `flex-wrap`: em 390px os itens não cabem numa linha só — sem ele o
              último item era CORTADO fora da viewport (apareceu ao acrescentar
              "Usuários" e revalidar por screenshot mobile). */}
          <ul className="flex flex-wrap gap-1 lg:flex-col lg:flex-nowrap">
            <li>
              <ClientNavLink
                href={base}
                active={isReconciliations}
                icon={<ListChecks className="h-4 w-4" aria-hidden="true" />}
              >
                Conciliações
              </ClientNavLink>
            </li>
            <li>
              <ClientNavLink
                href={accountsHref}
                active={isAccounts}
                icon={<Landmark className="h-4 w-4" aria-hidden="true" />}
              >
                Contas Bancárias
              </ClientNavLink>
            </li>
            <li>
              <ClientNavLink
                href={dashboardHref}
                active={isDashboard}
                icon={<LayoutDashboard className="h-4 w-4" aria-hidden="true" />}
              >
                Painel
              </ClientNavLink>
            </li>
            {canManageClientUsers && (
              <li>
                <ClientNavLink
                  href={usersHref}
                  active={isUsers}
                  icon={<UserCog className="h-4 w-4" aria-hidden="true" />}
                >
                  Usuários
                </ClientNavLink>
              </li>
            )}
          </ul>
        </nav>

        <div className="min-w-0 flex-1">{children}</div>
      </div>

      <EditClientModal open={editOpen} onOpenChange={setEditOpen} client={client} />
    </div>
  );
}

interface ClientNavLinkProps {
  href: string;
  active: boolean;
  icon: React.ReactNode;
  children: React.ReactNode;
}

function ClientNavLink({ href, active, icon, children }: ClientNavLinkProps) {
  return (
    <Link
      href={href}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'flex cursor-pointer items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors',
        'focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2',
        active ? 'bg-muted text-foreground font-medium' : 'text-muted-foreground hover:bg-muted',
      )}
    >
      {icon}
      <span>{children}</span>
    </Link>
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
      <div className="flex flex-col gap-6 lg:flex-row">
        <div className="space-y-2 lg:w-52">
          <div className="bg-muted h-9 w-full animate-pulse rounded-md" />
          <div className="bg-muted h-9 w-full animate-pulse rounded-md" />
        </div>
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
