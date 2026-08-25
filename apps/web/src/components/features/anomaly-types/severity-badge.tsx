/**
 * Badge de severidade — S15 FRONT 11.2. Cores espelham o padrão da tela de
 * revisão (vermelho = critical, âmbar = moderate, cinza = info).
 *
 * Severidade chega como string lenient do backend; valores fora do enum caem
 * num fallback neutro (não quebra o render se o catálogo legado tiver algum
 * valor exótico).
 *
 * `AnomalyTypeStatusBadge`: ativo (verde) / inativo (vermelho), igual à
 * tabela de usuários.
 */

import { cn } from '@/lib/utils';

const baseBadge =
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

// 86e2n39hb — tokens semânticos (flipam sozinhos entre os temas); o anel é o
// próprio token com alpha, mesmo padrão do Toaster (`border-success/30`).
const severityClasses: Record<string, string> = {
  critical: 'bg-destructive-muted text-destructive ring-destructive/30',
  moderate: 'bg-warning-muted text-warning ring-warning/30',
  info: 'bg-muted text-muted-foreground ring-border',
};

const severityLabels: Record<string, string> = {
  critical: 'Crítico',
  moderate: 'Moderado',
  info: 'Informativo',
};

export function SeverityBadge({ severity }: { severity: string }) {
  const cls = severityClasses[severity] ?? severityClasses['info']!;
  const label = severityLabels[severity] ?? severity;
  return <span className={cn(baseBadge, cls)}>{label}</span>;
}

export function AnomalyTypeStatusBadge({ active }: { active: boolean }) {
  return (
    <span
      className={cn(
        baseBadge,
        active
          ? 'bg-success-muted text-success ring-success/30'
          : 'bg-destructive-muted text-destructive ring-destructive/30',
      )}
    >
      {active ? 'Ativo' : 'Inativo'}
    </span>
  );
}
