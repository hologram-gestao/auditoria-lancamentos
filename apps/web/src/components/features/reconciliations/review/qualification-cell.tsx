'use client';

/**
 * Célula "Análise" da aba Movimentações (S19 FRONT 12.2).
 *
 * Recebe a lista de anomalias de qualificação NÃO resolvidas atreladas a uma
 * movimentação e renderiza um ícone + tooltip indicando a severidade:
 *   - `qualificacao_incoerente`              → ❌ vermelho (XCircle)
 *   - `qualificacao_suspeita` / `padrao_quebrado` / `valor_outlier`
 *                                            → ⚠️ âmbar (AlertTriangle)
 *   - sem anomalia pendente                  → ✅ verde (CheckCircle2)
 *
 * Quando o ícone é clicável (warning/critical), o consumidor passa
 * `onOpenOverride` — o clique abre o `QualificationOverrideDialog` no parent.
 */

import { AlertTriangle, CheckCircle2, XCircle } from 'lucide-react';

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import type { AnomalyItem } from '@/lib/api/reconciliations';
import { cn } from '@/lib/utils';

/** Códigos cobertos pelo indicador de qualificação (S19 BACK 12.1). */
export const QUALIFICATION_CODES = [
  'qualificacao_suspeita',
  'qualificacao_incoerente',
  'padrao_quebrado',
  'valor_outlier',
] as const;

export type QualificationCode = (typeof QUALIFICATION_CODES)[number];

const QUALIFICATION_CODE_SET = new Set<string>(QUALIFICATION_CODES);

export function isQualificationAnomaly(anomaly: AnomalyItem): boolean {
  return QUALIFICATION_CODE_SET.has(anomaly.anomaly_type.code);
}

type Severity = 'critical' | 'warning' | 'ok';

function severityFor(anomalies: AnomalyItem[]): Severity {
  if (anomalies.length === 0) return 'ok';
  const hasCritical = anomalies.some((a) => a.anomaly_type.code === 'qualificacao_incoerente');
  return hasCritical ? 'critical' : 'warning';
}

interface QualificationCellProps {
  /** Anomalias de qualificação NÃO resolvidas da movimentação. */
  anomalies: AnomalyItem[];
  /**
   * Disparado quando o usuário clica no indicador de warning/critical.
   * Não recebe nada porque o parent já sabe qual linha gerou o clique
   * (callback é construído por row).
   */
  onOpenOverride: () => void;
}

export function QualificationCell({ anomalies, onOpenOverride }: QualificationCellProps) {
  const severity = severityFor(anomalies);

  if (severity === 'ok') {
    return (
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              className="inline-flex items-center rounded-full bg-emerald-50 p-1 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
              aria-label="Qualificação coerente"
            >
              <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
            </span>
          </TooltipTrigger>
          <TooltipContent side="top" className="text-xs leading-snug">
            Qualificação coerente.
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  const Icon = severity === 'critical' ? XCircle : AlertTriangle;
  const colorClasses =
    severity === 'critical'
      ? 'bg-red-50 text-red-700 hover:bg-red-100 dark:bg-red-950/40 dark:text-red-300'
      : 'bg-amber-50 text-amber-700 hover:bg-amber-100 dark:bg-amber-950/40 dark:text-amber-300';
  const ariaLabel = severity === 'critical' ? 'Qualificação incoerente' : 'Qualificação suspeita';
  const tooltipBody = buildTooltipBody(anomalies);

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={onOpenOverride}
            aria-label={ariaLabel}
            className={cn(
              'focus-visible:ring-ring inline-flex items-center rounded-full p-1 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2',
              colorClasses,
            )}
          >
            <Icon className="h-4 w-4" aria-hidden="true" />
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-sm whitespace-pre-line text-xs leading-snug">
          {tooltipBody}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/**
 * Concatena `context` das anomalias em ordem de severidade (incoerente primeiro)
 * para o tooltip. Sem context viável → fallback pro nome do tipo.
 */
function buildTooltipBody(anomalies: AnomalyItem[]): string {
  const sorted = [...anomalies].sort((a, b) => {
    const ranks: Record<string, number> = {
      qualificacao_incoerente: 0,
      qualificacao_suspeita: 1,
      padrao_quebrado: 2,
      valor_outlier: 3,
    };
    const rankA = ranks[a.anomaly_type.code] ?? 99;
    const rankB = ranks[b.anomaly_type.code] ?? 99;
    return rankA - rankB;
  });
  return sorted
    .map((a) => {
      const ctx = a.context?.trim();
      return ctx !== undefined && ctx.length > 0 ? `• ${ctx}` : `• ${a.anomaly_type.name}`;
    })
    .join('\n');
}
