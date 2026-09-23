'use client';

/**
 * A seção "Origens de dado" do cliente (Sprint 9 / R4 · R5 · R7).
 *
 * É a única tela que responde **sempre 200** mesmo sem origem — exceção
 * deliberada do R6 à taxonomia 409, porque é aqui que a origem se conecta.
 * Mandar 409 aqui deixaria o parceiro sem conseguir ABRIR o cliente que acabou
 * de cadastrar.
 *
 * Duas camadas, e a ordem importa:
 *
 *   1. **Bloco de estado**, a partir de `origin_status` do detalhe — os três
 *      estados com copy própria. `erro` diz "origem com erro" e oferece
 *      **Reconectar**; nunca "sem origem", que mandaria criar uma conexão que
 *      já existe (o defeito que o R7 nomeia).
 *   2. **Lista das origens** (0..N), com as quatro ações por linha. Tudo isso
 *      some para quem não tem `manage_client_connections` — o estado e a lista
 *      FICAM (o operador precisa saber por que a conciliação dele não roda),
 *      as ações não.
 *
 * Estado por **token semântico**, nunca `opacity` na linha (ADR-007-FE).
 */

import { Loader2, Plus, RefreshCw, SquarePen, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCard,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useClientConnections, useTestStoredConnection } from '@/hooks/use-client-connections';
import { ApiError } from '@/lib/api/client';
import { DEFAULT_PROVIDER_LABEL } from '@/lib/api/client-connections';
import { hasPermission } from '@/lib/authz';
import type { ClientConnection, OriginStatus } from '@/lib/contracts';
import { formatLastCheckedAt, formatSyncedAt } from '@/lib/format';
import { ORIGIN_STATUS_COPY } from '@/lib/origin-state';
import { useAuthStore } from '@/stores/auth';

import { ConnectionStatusBadge } from './connection-badges';
import { ConnectionDeleteConfirm } from './connection-delete-confirm';
import { ConnectionFormDrawer } from './connection-form-drawer';

interface ClientConnectionsSectionProps {
  clientId: string;
  /** `origin_status` do detalhe do cliente — derivado das conexões no servidor. */
  originStatus: OriginStatus;
  /** Cliente ENCERRADO é só-leitura: o servidor nega toda escrita com 409 (§4.12). */
  isClosed: boolean;
}

export function ClientConnectionsSection({
  clientId,
  originStatus,
  isClosed,
}: ClientConnectionsSectionProps) {
  const currentUser = useAuthStore((s) => s.user);
  const canManage = hasPermission(currentUser, 'manage_client_connections') && !isClosed;

  const { data, isLoading, isFetching, isError, error, refetch } = useClientConnections(clientId);
  const testMutation = useTestStoredConnection(clientId);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<ClientConnection | null>(null);
  const [deleting, setDeleting] = useState<ClientConnection | null>(null);
  // Qual linha está testando — o spinner precisa ficar no botão clicado, e não
  // em todos: `mutation.isPending` sozinho acenderia a coluna inteira.
  const [testingId, setTestingId] = useState<string | null>(null);

  const connections = data ?? [];
  const activeCount = connections.filter((c) => c.status === 'ativa').length;
  const statusCopy = ORIGIN_STATUS_COPY[originStatus];
  // A coluna "Ações" só existe para quem escreve — senão o cabeçalho anunciaria
  // uma coluna permanentemente vazia.
  const columnCount = canManage ? 5 : 4;

  function openCreate() {
    setEditing(null);
    setFormOpen(true);
  }

  function openEdit(connection: ClientConnection) {
    setEditing(connection);
    setFormOpen(true);
  }

  async function handleTest(connection: ClientConnection) {
    setTestingId(connection.id);
    try {
      const updated = await testMutation.mutateAsync(connection.id);
      if (updated.status === 'ativa') {
        toast.success(`${updated.label}: credencial aceita.`);
      } else {
        // Credencial recusada NÃO é sucesso, mas também não é falha do sistema:
        // a conexão ficou marcada como erro e a credencial continua lá.
        toast.error(`${updated.label}: a origem recusou a credencial. Atualize-a para reconectar.`);
      }
    } catch (err) {
      toast.error(err instanceof ApiError ? err.userMessage : 'Não foi possível testar a origem.');
    } finally {
      setTestingId(null);
    }
  }

  return (
    <section aria-labelledby="connections-heading" className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h3 id="connections-heading" className="text-base font-semibold">
            Origens de dado
          </h3>
          <p className="text-muted-foreground text-sm">
            De onde este cliente traz contas e lançamentos. Um cliente pode ter nenhuma, uma ou
            várias.
          </p>
        </div>
        {canManage && (
          <Button type="button" onClick={openCreate}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            Conectar origem
          </Button>
        )}
      </div>

      {/* Bloco de estado — os três, com copy própria. `role="status"`: é uma
          etapa de configuração, não uma falha (ver `origin-state-notice`). */}
      <div
        role="status"
        data-origin-status={originStatus}
        className="bg-card space-y-2 rounded-lg border p-4"
      >
        {/* Sem o selo aqui de propósito: ele repetiria PALAVRA POR PALAVRA o
            título ("Origem com erro" nos dois), o que num leitor de tela vira
            a mesma frase duas vezes. O selo é da LISTA de clientes, onde é a
            única coisa que cabe; aqui o espaço permite a frase inteira. */}
        <p className="text-sm font-medium">{statusCopy.title}</p>
        <p className="text-muted-foreground text-sm">{statusCopy.description}</p>
        {canManage && statusCopy.actionLabel !== null && (
          <Button type="button" variant="outline" size="sm" onClick={openCreate}>
            {statusCopy.actionLabel}
          </Button>
        )}
      </div>

      <div aria-busy={isFetching}>
        {isError ? (
          <div
            role="alert"
            className="bg-destructive/5 border-destructive/30 text-destructive flex flex-col items-start gap-3 rounded-lg border p-4 text-sm sm:flex-row sm:items-center sm:justify-between"
          >
            <span>
              {error instanceof ApiError
                ? error.userMessage
                : 'Não foi possível carregar as origens.'}
            </span>
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              Tentar novamente
            </Button>
          </div>
        ) : (
          <TableCard>
            <Table fill scrollRegionLabel="Origens conectadas a este cliente (rolável)">
              <TableHeader>
                <TableRow>
                  <TableHead className="whitespace-nowrap">Origem</TableHead>
                  <TableHead className="whitespace-nowrap">Tipo</TableHead>
                  <TableHead className="whitespace-nowrap">Situação</TableHead>
                  <TableHead className="whitespace-nowrap">Última verificação</TableHead>
                  {canManage && (
                    <TableHead className="w-36 whitespace-nowrap text-right">Ações</TableHead>
                  )}
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  <SkeletonRows columnCount={columnCount} />
                ) : connections.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={columnCount} className="py-12">
                      <EmptyState canManage={canManage} onConnect={openCreate} />
                    </TableCell>
                  </TableRow>
                ) : (
                  connections.map((connection) => (
                    <TableRow key={connection.id}>
                      <TableCell className="font-medium">
                        {connection.label}
                        <span className="text-muted-foreground block text-xs">
                          {formatSyncedAt(connection.accounts_synced_at)}
                        </span>
                      </TableCell>
                      <TableCell className="text-muted-foreground whitespace-nowrap">
                        {DEFAULT_PROVIDER_LABEL[connection.provider_type] ??
                          connection.provider_type}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        <ConnectionStatusBadge status={connection.status} />
                      </TableCell>
                      <TableCell className="text-muted-foreground whitespace-nowrap text-sm">
                        {formatLastCheckedAt(connection.last_checked_at)}
                      </TableCell>
                      {canManage && (
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => void handleTest(connection)}
                              disabled={testingId !== null}
                              aria-label={`Testar ${connection.label} novamente`}
                            >
                              {testingId === connection.id ? (
                                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                              ) : (
                                <RefreshCw className="h-4 w-4" aria-hidden="true" />
                              )}
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => openEdit(connection)}
                              aria-label={`Editar ${connection.label}`}
                            >
                              <SquarePen className="h-4 w-4" aria-hidden="true" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => setDeleting(connection)}
                              aria-label={`Remover ${connection.label}`}
                            >
                              <Trash2 className="text-destructive h-4 w-4" aria-hidden="true" />
                            </Button>
                          </div>
                        </TableCell>
                      )}
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </TableCard>
        )}
      </div>

      {/* Gaveta e confirmação só existem para quem escreve — esconder o botão
          e deixar o componente montado o mantém alcançável por teclado. */}
      {canManage && (
        <>
          {/* Remount por `key`: abrir "editar" depois de "conectar" (ou trocar
              de origem) precisa nascer com o formulário limpo. */}
          <ConnectionFormDrawer
            key={editing?.id ?? 'new'}
            open={formOpen}
            onOpenChange={setFormOpen}
            clientId={clientId}
            connection={editing}
            connections={connections}
          />
          <ConnectionDeleteConfirm
            open={deleting !== null}
            onOpenChange={(open) => !open && setDeleting(null)}
            clientId={clientId}
            connection={deleting}
            activeCount={activeCount}
          />
        </>
      )}
    </section>
  );
}

function SkeletonRows({ columnCount }: { columnCount: number }) {
  return (
    <>
      {Array.from({ length: 2 }).map((_, index) => (
        <TableRow key={index} aria-hidden="true">
          {Array.from({ length: columnCount }).map((__, cell) => (
            <TableCell key={cell}>
              <div className="bg-muted h-4 w-full animate-pulse rounded" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}

function EmptyState({ canManage, onConnect }: { canManage: boolean; onConnect: () => void }) {
  if (!canManage) {
    return (
      <div className="space-y-1 text-center">
        <p className="text-sm font-medium">Nenhuma origem conectada</p>
        <p className="text-muted-foreground text-sm">
          Enquanto não houver origem, as telas que dependem dela ficam indisponíveis. Fale com quem
          administra este cliente.
        </p>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center gap-3 text-center">
      <div className="space-y-1">
        <p className="text-sm font-medium">Nenhuma origem conectada</p>
        <p className="text-muted-foreground text-sm">
          Conecte o sistema de onde este cliente traz contas e lançamentos.
        </p>
      </div>
      <Button type="button" onClick={onConnect}>
        <Plus className="h-4 w-4" aria-hidden="true" />
        Conectar origem
      </Button>
    </div>
  );
}
