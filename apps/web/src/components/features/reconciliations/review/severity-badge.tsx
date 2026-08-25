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
        className="bg-destructive-muted text-destructive inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
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
        className="bg-warning-muted text-warning inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
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
        className="bg-info-muted text-info inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
        aria-label="Severidade informativa"
      >
        <Info className="h-3 w-3" aria-hidden="true" />
        Informativa
      </span>
    );
  }
  return (
    <span className="bg-muted text-muted-foreground inline-flex rounded-full px-2 py-0.5 text-xs font-medium">
      {severity}
    </span>
  );
}
