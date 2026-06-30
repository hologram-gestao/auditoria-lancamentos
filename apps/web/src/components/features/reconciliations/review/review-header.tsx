'use client';

/**
 * Header fixo da tela de revisão (FRONT 9.11). Compõe:
 *   - Breadcrumb (Clientes › Nome › Conciliação MÊS/ANO)
 *   - Nome da conta bancária (resolvido via cache `useClientDetail`)
 *   - Contadores em tempo real (atualizam via `useSessionStatus` que é
 *     invalidado pelas mutations de file-entry e anomaly).
 *   - Botão "Exportar Relatório" — chama BACK 10.1 (S14) e dispara o
 *     download do XLSX via objectURL.
 *
 * Decisão sobre dados estáticos vs vivos:
 *   - `referenceMonth`, `accountName` são estáticos; vêm via props pra
 *     o `<ReviewScreen>` resolver UMA vez (lista paginada de
 *     reconciliations + accounts do client detail).
 *   - Contadores vivos vêm do `status` que o orquestrador passa por
 *     props (já invalidado por todas as mutations).
 */

import { AlertTriangle, ChevronRight, Download, FileWarning, Loader2 } from 'lucide-react';
import Link from 'next/link';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { useExportReconciliation } from '@/hooks/use-reconciliations';
import { ApiError, NetworkError } from '@/lib/api/client';
import { cn } from '@/lib/utils';

interface ReviewHeaderProps {
  clientId: string;
  clientName: string;
  sessionId: string;
  /** Texto formatado em PT-BR (ex: "Abril/2026"). */
  referenceMonthLabel: string;
  /** Nome da conta (sem banco) — undefined enquanto o cache hidrata. */
  accountName: string | undefined;
  /** FRONT 1.8: cartão de crédito → badge azul + título "Cartão". */
  isCard: boolean;
  /** Conta aplicação (CA) → badge verde + título "Aplicação". */
  isInvestment: boolean;
  /** Status vivo da sessão para contadores. */
  counts: {
    conciliated: number;
    semOmie: number;
    omieSemArquivo: number;
    anomaly: number;
  };
}

export function ReviewHeader({
  clientId,
  clientName,
  sessionId,
  referenceMonthLabel,
  accountName,
  isCard,
  isInvestment,
  counts,
}: ReviewHeaderProps) {
  const exportMutation = useExportReconciliation(sessionId);
  // Título: "Conciliação · {tipo} · {conta} · {Mês/Ano}" (FRONT 1.8). Segmentos
  // vazios (conta ainda hidratando, mês ausente) são omitidos.
  let accountTypeLabel = 'Conta Corrente';
  if (isCard) accountTypeLabel = 'Cartão';
  else if (isInvestment) accountTypeLabel = 'Aplicação';
  const title = ['Conciliação', accountTypeLabel, accountName, referenceMonthLabel]
    .filter((seg): seg is string => Boolean(seg))
    .join(' · ');

  function handleExport(): void {
    exportMutation.mutate(undefined, {
      onSuccess: ({ blob, filename }) => {
        // Fallback de filename: backend manda sempre, mas se algum dia
        // o header não vier (proxy reescrevendo, mock em teste), evitamos
        // baixar "blob" sem extensão.
        const finalName = filename ?? `Conciliacao_${referenceMonthLabel.replace('/', '-')}.xlsx`;
        triggerBrowserDownload(blob, finalName);
      },
      onError: (err) => {
        const userMessage = resolveExportErrorMessage(err);
        toast.error(userMessage);
      },
    });
  }

  return (
    <header className="space-y-3 border-b pb-4">
      <nav aria-label="Trilha de navegação" className="text-muted-foreground text-sm">
        <ol className="flex flex-wrap items-center gap-1">
          <li>
            <Link href="/clientes" className="hover:underline">
              Clientes
            </Link>
          </li>
          <ChevronRight className="h-3 w-3" aria-hidden="true" />
          <li>
            <Link href={`/clientes/${clientId}`} className="hover:underline">
              {clientName}
            </Link>
          </li>
          <ChevronRight className="h-3 w-3" aria-hidden="true" />
          <li className="text-foreground font-medium">Conciliação {referenceMonthLabel}</li>
        </ol>
      </nav>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <AccountTypeBadge isCard={isCard} isInvestment={isInvestment} />
          <h1 className="text-2xl font-semibold">{title}</h1>
        </div>

        <Button
          size="sm"
          onClick={handleExport}
          disabled={exportMutation.isPending}
          aria-label="Exportar relatório Excel"
        >
          {exportMutation.isPending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Gerando...
            </>
          ) : (
            <>
              <Download className="h-4 w-4" aria-hidden="true" />
              Exportar Relatório
            </>
          )}
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <CountChip
          icon={<span aria-hidden="true">✅</span>}
          label="conciliados"
          value={counts.conciliated}
          className="bg-emerald-50 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-200"
        />
        <CountChip
          icon={<AlertTriangle className="h-3 w-3" aria-hidden="true" />}
          label="sem Omie"
          value={counts.semOmie}
          className="bg-amber-50 text-amber-800 dark:bg-amber-900/30 dark:text-amber-200"
        />
        <CountChip
          icon={<FileWarning className="h-3 w-3" aria-hidden="true" />}
          label="Omie sem arquivo"
          value={counts.omieSemArquivo}
          className="bg-red-50 text-red-800 dark:bg-red-900/30 dark:text-red-200"
        />
        <CountChip
          icon={<span aria-hidden="true">🔶</span>}
          label="anomalias"
          value={counts.anomaly}
          className={cn(
            'bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200',
            counts.anomaly > 0 &&
              'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-200',
          )}
        />
      </div>
    </header>
  );
}

/** Badge do tipo de conta: Conta Corrente cinza / Cartão azul / Aplicação verde. */
function AccountTypeBadge({ isCard, isInvestment }: { isCard: boolean; isInvestment: boolean }) {
  let label = 'Conta Corrente';
  let colorClass =
    'bg-slate-100 text-slate-700 ring-slate-200 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-700';
  if (isCard) {
    label = 'Cartão de Crédito';
    colorClass =
      'bg-blue-50 text-blue-700 ring-blue-200 dark:bg-blue-950 dark:text-blue-300 dark:ring-blue-900';
  } else if (isInvestment) {
    label = 'Conta Aplicação';
    colorClass =
      'bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:ring-emerald-900';
  }
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold ring-1 ring-inset',
        colorClass,
      )}
    >
      {label}
    </span>
  );
}

interface CountChipProps {
  icon: React.ReactNode;
  label: string;
  value: number;
  className?: string;
}

function CountChip({ icon, label, value, className }: CountChipProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium',
        className,
      )}
    >
      {icon}
      <span className="font-semibold">{value}</span>
      <span>{label}</span>
    </span>
  );
}

/**
 * Cria um link temporário e dispara o `click()` — padrão idiomático para
 * download de blob. `URL.revokeObjectURL` no fim libera a memória do
 * blob (Chrome/Firefox seguram a referência indefinidamente sem o revoke).
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

/**
 * Mapeia erros conhecidos do export para mensagens PT-BR amigáveis.
 *
 * Reusa o `userMessage` do backend (já localizado) quando disponível —
 * para 404/409/auth o servidor já manda em PT-BR. Fallback genérico
 * cobre os casos restantes (5xx, rede, parse).
 */
function resolveExportErrorMessage(err: Error): string {
  if (err instanceof ApiError) {
    return err.userMessage;
  }
  if (err instanceof NetworkError) {
    return err.userMessage;
  }
  return 'Não foi possível gerar o relatório. Tente novamente em instantes.';
}
