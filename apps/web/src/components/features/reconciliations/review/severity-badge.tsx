/**
 * Badge tricolor de severidade de anomalia (Doc §14.6).
 * Lenient com a string vinda do back — fallback cinza neutro.
 */
import { AlertOctagon, AlertTriangle, Info } from 'lucide-react';

interface SeverityBadgeProps {
  severity: string;
}

export function SeverityBadge({ severity }: SeverityBadgeProps) {
  if (severity === 'critical') {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-800 dark:bg-red-900/40 dark:text-red-200"
        aria-label="Severidade crítica"
      >
        <AlertOctagon className="h-3 w-3" aria-hidden="true" />
        Crítica
      </span>
    );
  }
  if (severity === 'moderate') {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
        aria-label="Severidade moderada"
      >
        <AlertTriangle className="h-3 w-3" aria-hidden="true" />
        Moderada
      </span>
    );
  }
  if (severity === 'info') {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full bg-sky-100 px-2 py-0.5 text-xs font-medium text-sky-800 dark:bg-sky-900/40 dark:text-sky-200"
        aria-label="Severidade informativa"
      >
        <Info className="h-3 w-3" aria-hidden="true" />
        Informativa
      </span>
    );
  }
  return (
    <span className="inline-flex rounded-full bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-700 dark:bg-slate-700 dark:text-slate-200">
      {severity}
    </span>
  );
}
