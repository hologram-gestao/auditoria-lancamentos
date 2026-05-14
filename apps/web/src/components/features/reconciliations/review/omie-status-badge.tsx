/**
 * Badge do status Omie (Conciliado / Atrasado / Previsto / outros).
 * Cores conforme Doc §14.4 (Atrasado vermelho, Previsto amarelo).
 */
interface OmieStatusBadgeProps {
  status: string;
}

export function OmieStatusBadge({ status }: OmieStatusBadgeProps) {
  const lower = status.toLowerCase();
  if (lower === 'atrasado') {
    return (
      <span className="inline-flex rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-800 dark:bg-red-900/40 dark:text-red-200">
        Atrasado
      </span>
    );
  }
  if (lower === 'previsto') {
    return (
      <span className="inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">
        Previsto
      </span>
    );
  }
  if (lower === 'conciliado') {
    return (
      <span className="inline-flex rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200">
        Conciliado
      </span>
    );
  }
  return (
    <span className="inline-flex rounded-full bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-700 dark:bg-slate-700 dark:text-slate-200">
      {status}
    </span>
  );
}
