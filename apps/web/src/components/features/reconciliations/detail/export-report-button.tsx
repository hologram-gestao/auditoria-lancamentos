'use client';

/**
 * Botão "Exportar relatório" (Excel, S14 / BACK 10.1).
 *
 * Extraído do antigo `ReviewHeader` para ser reusado pelo novo cabeçalho do
 * detalhe. Botão async: `disabled` + spinner enquanto gera, reabilita em
 * sucesso OU erro.
 */

import { Download, Loader2 } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { useExportReconciliation } from '@/hooks/use-reconciliations';
import { ApiError, NetworkError } from '@/lib/api/client';

interface ExportReportButtonProps {
  sessionId: string;
  /** Usado só no fallback de nome do arquivo. */
  referenceMonthLabel: string;
}

export function ExportReportButton({ sessionId, referenceMonthLabel }: ExportReportButtonProps) {
  const exportMutation = useExportReconciliation(sessionId);

  function handleExport(): void {
    exportMutation.mutate(undefined, {
      onSuccess: ({ blob, filename }) => {
        // O backend manda o nome no Content-Disposition; o fallback evita
        // baixar "blob" sem extensão se um proxy reescrever o header.
        const finalName =
          filename ?? `Conciliacao_${referenceMonthLabel.replace(/[ /]/g, '-')}.xlsx`;
        triggerBrowserDownload(blob, finalName);
      },
      onError: (err) => toast.error(resolveExportErrorMessage(err)),
    });
  }

  return (
    <Button
      variant="outline"
      size="sm"
      onClick={handleExport}
      disabled={exportMutation.isPending}
      aria-label="Exportar relatório Excel"
      aria-live="polite"
    >
      {exportMutation.isPending ? (
        <>
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Gerando…
        </>
      ) : (
        <>
          <Download className="h-4 w-4" aria-hidden="true" />
          Exportar relatório
        </>
      )}
    </Button>
  );
}

/**
 * Cria um link temporário e dispara o `click()` — padrão idiomático para
 * download de blob. `URL.revokeObjectURL` no fim libera a memória (Chrome e
 * Firefox seguram a referência indefinidamente sem o revoke).
 */
function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** Reusa o `userMessage` do backend (já em PT-BR) quando existe. */
function resolveExportErrorMessage(err: Error): string {
  if (err instanceof ApiError || err instanceof NetworkError) return err.userMessage;
  return 'Não foi possível gerar o relatório. Tente novamente em instantes.';
}
