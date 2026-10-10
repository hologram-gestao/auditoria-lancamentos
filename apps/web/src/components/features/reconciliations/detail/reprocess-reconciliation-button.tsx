'use client';

/**
 * "Reprocessar com o Omie" — cruzar de novo uma conciliação CONCLUÍDA (86e3n70q9).
 *
 * Origem: reunião de 08/10/2026. Depois de lançar no Omie as compras que
 * faltavam, a conciliação não se atualizava e o caminho era excluir e refazer.
 * O servidor (`POST /reconciliations/{id}/reprocess`) aceita `reviewing` e
 * `done`, mantém as linhas do arquivo e NUNCA toca no registro do que já foi
 * lançado no Omie; o que ele limpa é a revisão.
 *
 * `AlertDialog`, e não clique direto: a ação apaga trabalho do analista, e o
 * corpo diz o QUÊ antes de confirmar (§4.9: o servidor decide, a tela avisa).
 * Depois do sucesso a tela não navega: o detalhe é invalidado, volta como
 * `processing` e o `useSessionDetail` retoma o polling de sempre.
 *
 * Quem decide se o botão existe é quem renderiza (status, cliente encerrado e
 * `run_reconciliation`, a permissão da rota).
 */

import { Loader2, RefreshCw } from 'lucide-react';
import { type MouseEvent, useState } from 'react';
import { toast } from 'sonner';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { useReprocessReconciliation } from '@/hooks/use-reconciliations';
import { ApiError } from '@/lib/api/client';

export const REPROCESS_SUCCESS_TOAST =
  'Reprocessamento iniciado. A conciliação volta a ficar disponível quando o cruzamento terminar.';

/** O que a revisão perde ao reprocessar. Fonte única: o diálogo e o teste leem daqui. */
export const REPROCESS_LOSSES: readonly string[] = [
  'as notas e as ações registradas nas movimentações e nas divergências do Omie;',
  'as anomalias, com as resoluções e os vereditos dados a elas;',
  'a análise da IA, que roda de novo no fim do cruzamento.',
];

interface ReprocessReconciliationButtonProps {
  sessionId: string;
  clientId: string;
}

export function ReprocessReconciliationButton({
  sessionId,
  clientId,
}: ReprocessReconciliationButtonProps) {
  const [open, setOpen] = useState(false);
  const reprocess = useReprocessReconciliation(sessionId, clientId);

  function handleOpenChange(next: boolean) {
    if (reprocess.isPending) return;
    setOpen(next);
  }

  async function handleConfirm(event: MouseEvent<HTMLButtonElement>) {
    // O `AlertDialogAction` fecha ao clicar; aqui quem fecha é o sucesso.
    event.preventDefault();
    if (reprocess.isPending) return;
    try {
      await reprocess.mutateAsync();
      toast.success(REPROCESS_SUCCESS_TOAST);
      setOpen(false);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível reprocessar a conciliação.',
      );
    }
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <RefreshCw className="h-4 w-4" aria-hidden="true" />
        Reprocessar com o Omie
      </Button>
      <AlertDialog open={open} onOpenChange={handleOpenChange}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reprocessar com o Omie?</AlertDialogTitle>
            <AlertDialogDescription>
              Os lançamentos são buscados de novo no Omie e cruzados com as linhas do arquivo, que
              continuam as mesmas. Use depois de lançar ou corrigir algo no Omie.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-3 text-sm">
            <div>
              <p className="font-medium">A revisão recomeça do zero. Saem:</p>
              <ul className="text-muted-foreground mt-1 list-disc space-y-1 pl-5">
                {REPROCESS_LOSSES.map((loss) => (
                  <li key={loss}>{loss}</li>
                ))}
              </ul>
            </div>
            <p className="text-muted-foreground">
              O que já foi lançado no Omie continua lançado: nenhuma compra é enviada de novo, e o
              cruzamento volta a encontrá-la no extrato.
            </p>
          </div>
          <AlertDialogFooter className="gap-2 sm:justify-between">
            <AlertDialogCancel disabled={reprocess.isPending}>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              variant="default"
              disabled={reprocess.isPending}
              onClick={(event) => void handleConfirm(event)}
            >
              {reprocess.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
              )}
              {reprocess.isPending ? 'Reprocessando…' : 'Reprocessar'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
