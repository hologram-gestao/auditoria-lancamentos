'use client';

/**
 * Uma linha da Lista de Conciliações (Sprint 4 / R1).
 *
 * A LINHA INTEIRA é o alvo de navegação para o detalhe. Implementada como um
 * `<article>` com `onClick` + `role="link"` + `tabIndex` + handler de teclado
 * (Enter/Espaço), e não como um `<a>` embrulhando tudo, porque dentro dela há
 * botões de ação: `<button>` aninhado em `<a>` é HTML inválido e quebra o
 * comportamento nativo. As ações internas chamam `stopPropagation` para que
 * clicar em "Excluir" não navegue junto.
 *
 * O contraponto de acessibilidade: o título continua sendo um `<Link>` real,
 * então navegação por teclado/leitor de tela tem um destino de verdade e a
 * pessoa consegue abrir em nova aba.
 */

import {
  AlertCircle,
  CheckCircle2,
  Files,
  Loader2,
  RefreshCw,
  Trash2,
  XCircle,
} from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { toast } from 'sonner';

import { ReconciliationStatusBadge } from '@/components/features/clients/reconciliation-status-badge';
import { AuthorLabel } from '@/components/features/reconciliations/author-label';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  useCancelReconciliation,
  useDiscardReconciliation,
  useReprocessReconciliation,
} from '@/hooks/use-reconciliations';
import { ApiError } from '@/lib/api/client';
import type { ReconciliationSessionSummary } from '@/lib/api/clients';
import { formatCreatedAt, formatReferenceMonth } from '@/lib/format';

interface ReconciliationListItemProps {
  clientId: string;
  session: ReconciliationSessionSummary;
  accountName: string;
}

export function ReconciliationListItem({
  clientId,
  session,
  accountName,
}: ReconciliationListItemProps) {
  const router = useRouter();
  const [confirmDiscardOpen, setConfirmDiscardOpen] = useState(false);
  const [confirmCancelOpen, setConfirmCancelOpen] = useState(false);
  const reprocessMutation = useReprocessReconciliation(session.id, clientId);
  const discardMutation = useDiscardReconciliation(session.id, clientId);
  const cancelMutation = useCancelReconciliation(session.id, clientId);

  const href = `/clientes/${clientId}/conciliacao/${session.id}`;
  const isProcessing = session.status === 'processing';
  const isError = session.status === 'error';
  const isProcessed = session.status === 'reviewing' || session.status === 'done';
  const referenceLabel = formatReferenceMonth(session.reference_month);
  const createdAtLabel = formatCreatedAt(session.created_at);

  function openDetail() {
    router.push(href);
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLElement>) {
    // Só reage quando o foco está na própria linha — senão Enter dentro de um
    // botão de ação navegaria além de executar a ação.
    if (event.target !== event.currentTarget) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openDetail();
    }
  }

  async function handleReprocess() {
    try {
      await reprocessMutation.mutateAsync();
      toast.success('Reprocessamento iniciado.');
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível reprocessar a conciliação.',
      );
    }
  }

  async function handleDiscard() {
    try {
      await discardMutation.mutateAsync();
      toast.success('Conciliação excluída.');
      setConfirmDiscardOpen(false);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível excluir a conciliação.',
      );
    }
  }

  async function handleCancel() {
    try {
      await cancelMutation.mutateAsync();
      toast.success('Processamento cancelado.');
      setConfirmCancelOpen(false);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível cancelar o processamento.',
      );
    }
  }

  return (
    <article
      role="link"
      tabIndex={0}
      onClick={openDetail}
      onKeyDown={handleKeyDown}
      aria-label={`Abrir conciliação de ${accountName} em ${referenceLabel}`}
      className="bg-card hover:border-primary/40 focus-visible:ring-ring cursor-pointer space-y-3 rounded-lg border p-4 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-0.5">
          <Link
            href={href}
            onClick={(e) => e.stopPropagation()}
            className="hover:underline focus-visible:underline focus-visible:outline-none"
          >
            <span className="block truncate text-sm font-medium leading-tight">{accountName}</span>
          </Link>
          <p className="text-muted-foreground text-xs">{referenceLabel}</p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <ReconciliationStatusBadge status={session.status} />
          <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
            <Files className="h-3.5 w-3.5" aria-hidden="true" />
            {session.total_files} arquivo{session.total_files === 1 ? '' : 's'}
          </span>
        </div>
      </div>

      {isProcessing && (
        <p className="text-muted-foreground flex items-center gap-2 text-sm">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Em processamento… você pode sair desta tela; avisamos quando terminar.
        </p>
      )}

      {/* Erro mostra CÓDIGO, nunca a linguagem interna (S2/R9). Fundo
          `destructive`, jamais âmbar. */}
      {isError && (
        <p className="text-destructive text-sm">
          Não foi possível concluir a conciliação
          {session.error_code ? ` (cód. ${session.error_code})` : ''}.
        </p>
      )}

      {isProcessed && (
        <div className="text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
          <span className="inline-flex items-center gap-1">
            <CheckCircle2 className="text-success h-3.5 w-3.5" aria-hidden="true" />
            {session.conciliated_count} conciliado{session.conciliated_count === 1 ? '' : 's'}
          </span>
          <span className="inline-flex items-center gap-1">
            <AlertCircle className="text-warning h-3.5 w-3.5" aria-hidden="true" />
            {session.sem_omie_count} sem Omie
          </span>
          <span className="inline-flex items-center gap-1">
            <XCircle className="text-destructive h-3.5 w-3.5" aria-hidden="true" />
            {session.omie_sem_arquivo_count} Omie sem arquivo
          </span>
        </div>
      )}

      <div className="text-muted-foreground flex flex-wrap items-center justify-between gap-2 text-xs">
        {/* 86e2n39f1 — QUEM fez, não só quando. Sem autor (payload antigo em
            cache), o texto de antes continua valendo. */}
        {session.created_by ? (
          <span>
            Conciliado por <AuthorLabel author={session.created_by} /> · {createdAtLabel}
          </span>
        ) : (
          <span>Criada em {createdAtLabel}</span>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {isProcessing && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                setConfirmCancelOpen(true);
              }}
              disabled={cancelMutation.isPending}
              className="text-destructive hover:text-destructive hover:bg-destructive/10"
            >
              <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
              Cancelar
            </Button>
          )}
          {isError && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                void handleReprocess();
              }}
              disabled={reprocessMutation.isPending || discardMutation.isPending}
              aria-live="polite"
            >
              {reprocessMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              {reprocessMutation.isPending ? 'Reprocessando…' : 'Tentar novamente'}
            </Button>
          )}
          {!isProcessing && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                setConfirmDiscardOpen(true);
              }}
              disabled={discardMutation.isPending || reprocessMutation.isPending}
              className="text-destructive hover:text-destructive hover:bg-destructive/10"
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
              Excluir
            </Button>
          )}
        </div>
      </div>

      {/* Diálogos vivem dentro do <article>, então cliques neles borbulhariam
          até o onClick da linha — `stopPropagation` no container corta isso. */}
      <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
        <Dialog open={confirmDiscardOpen} onOpenChange={setConfirmDiscardOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Excluir esta conciliação?</DialogTitle>
              <DialogDescription>
                A conciliação de <strong>{accountName}</strong> em <strong>{referenceLabel}</strong>{' '}
                sai da lista e a conta+mês fica livre para uma nova. Esta ação não pode ser desfeita
                pela interface — o registro fica preservado no banco apenas para auditoria.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setConfirmDiscardOpen(false)}
                disabled={discardMutation.isPending}
              >
                Voltar
              </Button>
              <Button
                type="button"
                variant="destructive"
                onClick={() => void handleDiscard()}
                disabled={discardMutation.isPending}
                aria-live="polite"
              >
                {discardMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                )}
                {discardMutation.isPending ? 'Excluindo…' : 'Excluir conciliação'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog open={confirmCancelOpen} onOpenChange={setConfirmCancelOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Cancelar o processamento?</DialogTitle>
              <DialogDescription>
                A conciliação de <strong>{accountName}</strong> em <strong>{referenceLabel}</strong>{' '}
                será interrompida e marcada como erro. Depois você poderá reprocessar ou excluir.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setConfirmCancelOpen(false)}
                disabled={cancelMutation.isPending}
              >
                Voltar
              </Button>
              <Button
                type="button"
                variant="destructive"
                onClick={() => void handleCancel()}
                disabled={cancelMutation.isPending}
                aria-live="polite"
              >
                {cancelMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <XCircle className="h-4 w-4" aria-hidden="true" />
                )}
                {cancelMutation.isPending ? 'Cancelando…' : 'Cancelar processamento'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </article>
  );
}
