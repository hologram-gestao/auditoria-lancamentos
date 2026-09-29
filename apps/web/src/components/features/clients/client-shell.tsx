'use client';

/**
 * Shell de navegação DENTRO de um cliente (Sprint 4 / R6).
 *
 * Antes, "Contas Bancárias" e "Histórico de Conciliações" eram duas seções
 * empilhadas na mesma página. A reunião de 07/07 pediu que virassem dois
 * DESTINOS distintos, com a Lista de Conciliações promovida a tela principal.
 * Este componente é a moldura comum a todas as páginas do cliente.
 *
 * Sem cabeçalho próprio (86e3fr9q3, feedback do Lucas em 28/09/2026): o
 * breadcrumb, o nome do cliente, os selos (status, categoria), o favorito e o
 * menu "Ações do cliente" saíram para a lista subir. O nome do cliente já está
 * no menu lateral; status, categoria e favorito ficam na lista de clientes; e
 * Editar/Encerrar moram na linha da lista (86e3fr9qj). O título de cada página
 * é o h1 da PRÓPRIA tela (Carteira, De-para, "Conta · Mês" no detalhe da
 * conciliação...): um título aqui repetiria o dela logo abaixo. A volta de uma
 * conciliação para a lista é o item "Conciliações" do menu lateral.
 *
 * Navegação (86e2n39h7 + 86e2n4pf9): o menu do cliente mora no `<aside>` do
 * shell (`SidebarNav`, em camadas) de `md` para cima e no drawer do hambúrguer
 * (`MobileNavDrawer`) abaixo disso. A árvore é uma só
 * (`features/navigation/nav-items`).
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

import { Archive } from 'lucide-react';
import Link from 'next/link';

import { AccessDenied } from '@/components/shared/access-denied';
import { Button } from '@/components/ui/button';
import { useClientDetail } from '@/hooks/use-clients';
import { ApiError } from '@/lib/api/client';
import { canAccessClient, homePathFor } from '@/lib/authz';
import { useAuthStore } from '@/stores/auth';

interface ClientShellProps {
  clientId: string;
  children: React.ReactNode;
}

export function ClientShell({ clientId, children }: ClientShellProps) {
  const currentUser = useAuthStore((s) => s.user);

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

  // 86e36pm1z — encerrado é só-leitura: o servidor nega toda escrita com 409.
  // Com o selo fora do cabeçalho, este aviso é o que explica por que as ações
  // sumiram das telas.
  const isClosed = client.closed_at != null;

  return (
    <div className="flex h-full flex-col gap-4">
      {isClosed && (
        <p className="bg-muted text-muted-foreground flex items-center gap-2 rounded-md border px-3 py-2 text-sm">
          <Archive className="h-4 w-4 shrink-0" aria-hidden="true" />
          Cliente encerrado: somente leitura
        </p>
      )}

      {/* `min-h-0` (ADR-007): sem ele o item flex cresce até a altura do
          conteúdo e as regiões internas (TableCard/ScrollRegion) nunca rolam —
          a barra de paginação voltaria a cobrir linhas (86e2u4nxg/86e2uca1d,
          pego pelo gate quando o layout virou coluna). */}
      <div className="min-h-0 min-w-0 flex-1">{children}</div>
    </div>
  );
}

function ClientShellSkeleton() {
  return (
    <div role="status" className="space-y-6" aria-busy="true" aria-label="Carregando cliente">
      <div className="bg-muted h-6 w-48 animate-pulse rounded" />
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
