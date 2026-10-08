/**
 * As peças de moldura do painel do cliente (86e3k1q54): o card, o skeleton e o
 * erro POR BLOCO. Cada bloco do painel carrega e falha sozinho: um 500 na
 * carteira não pode esconder o fechamento do mês, e o "Tentar novamente" refaz
 * só o que falhou.
 */
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api/client';
import { cn } from '@/lib/utils';

interface DashboardCardProps {
  /** Título do card. O `id` liga o `aria-labelledby` da seção. */
  title: string;
  /** Nível do título: `h3` dentro de um bloco com `h2`; `h2` quando o card É o bloco. */
  level?: 2 | 3;
  titleId: string;
  /** Ação do canto (link "Revisar anomalias" etc.). */
  footer?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

export function DashboardCard({
  title,
  titleId,
  level = 3,
  footer,
  children,
  className,
}: DashboardCardProps) {
  const Heading = level === 2 ? 'h2' : 'h3';
  return (
    <section
      aria-labelledby={titleId}
      className={cn(
        'bg-card flex min-w-0 flex-col gap-3 rounded-lg border p-4 shadow-sm',
        className,
      )}
    >
      <Heading id={titleId} className="text-muted-foreground text-sm font-medium">
        {title}
      </Heading>
      <div className="flex min-w-0 flex-1 flex-col gap-3">{children}</div>
      {footer !== undefined && <div className="pt-1 text-sm">{footer}</div>}
    </section>
  );
}

/** Esqueleto de UM card, com o nome do que está carregando. */
export function CardSkeleton({ label, className }: { label: string; className?: string }) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label={label}
      className={cn('bg-card h-40 animate-pulse rounded-lg border', className)}
    />
  );
}

/** Erro de UM bloco, com a mensagem do servidor quando houver e o retry dele. */
export function BlockError({
  error,
  fallback,
  onRetry,
}: {
  error: unknown;
  fallback: string;
  onRetry: () => void;
}) {
  return (
    <div
      role="alert"
      className="bg-destructive/5 border-destructive/30 text-destructive flex flex-col items-start gap-3 rounded-lg border p-4 text-sm sm:flex-row sm:items-center sm:justify-between"
    >
      <span>{error instanceof ApiError ? error.userMessage : fallback}</span>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Tentar novamente
      </Button>
    </div>
  );
}

/** Barra horizontal decorativa (os números estão no texto ao lado). */
export function ProgressBar({ percent, className }: { percent: number; className: string }) {
  const width = Math.max(0, Math.min(100, percent));
  return (
    <div aria-hidden="true" className="bg-muted h-2 w-full overflow-hidden rounded-full">
      <div className={cn('h-full rounded-full', className)} style={{ width: `${width}%` }} />
    </div>
  );
}
