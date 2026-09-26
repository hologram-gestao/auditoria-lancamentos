/**
 * Selos do de-para (Sprint 12 — FRONT 12.7 / R2 · R7).
 *
 * As QUATRO situações são estados diferentes com remédios diferentes, e a tela
 * precisa distingui-las sem depender só de cor (o texto do selo é o nome
 * acessível):
 *
 *   - `herdada` — veio do plano de contas da origem; ninguém confirmou ainda;
 *   - `confirmada` — uma pessoa decidiu (ou confirmou a herdada);
 *   - `nao_mapear` — DECISÃO tomada de deixar a categoria fora do destino.
 *     Não é pendência: por isso o selo é neutro e carrega o ícone de "fora";
 *   - `sem_decisao` — TRABALHO pendente: aviso.
 *
 * Cor só por token semântico, fundo de selo pelo par `-muted` (nunca alfa sobre
 * a linha — ver `client-titles-badges`).
 */
import { AlertTriangle, Ban } from 'lucide-react';

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import type { MappingSituation } from '@/lib/contracts';
import { cn } from '@/lib/utils';

const baseBadge =
  'inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

export const MAPPING_SITUATION_LABELS: Record<MappingSituation, string> = {
  herdada: 'Herdada da origem',
  confirmada: 'Confirmada',
  nao_mapear: 'Não mapear',
  sem_decisao: 'Sem decisão',
};

/** Rótulos do FILTRO — "Não mapear" diz que é decisão, para não ler como pendência. */
export const MAPPING_SITUATION_FILTER_LABELS: Record<MappingSituation, string> = {
  herdada: 'Herdadas da origem',
  confirmada: 'Confirmadas',
  nao_mapear: 'Não mapear (decidido)',
  sem_decisao: 'Sem decisão',
};

const SITUATION_CLASSES: Record<MappingSituation, string> = {
  herdada: 'bg-info-muted text-info ring-info/30',
  confirmada: 'bg-success-muted text-success ring-success/30',
  nao_mapear: 'bg-muted text-muted-foreground ring-border',
  sem_decisao: 'bg-warning-muted text-warning ring-warning/30',
};

/**
 * `situation` é enum FECHADO no contrato, mas chega do servidor em runtime: um
 * valor fora da whitelist cai num neutro com o texto cru — visível, e sem
 * derrubar a lista por causa de uma linha.
 */
export function MappingSituationBadge({ situation }: { situation: string }) {
  const known = situation as MappingSituation;
  return (
    <span className={cn(baseBadge, SITUATION_CLASSES[known] ?? SITUATION_CLASSES.nao_mapear)}>
      {known === 'nao_mapear' && <Ban className="h-3 w-3" aria-hidden="true" />}
      {MAPPING_SITUATION_LABELS[known] ?? situation}
    </span>
  );
}

/**
 * Indicador de DIVERGÊNCIA com a origem (R7): o plano de contas foi
 * re-sincronizado e a conta de demonstrativo da categoria mudou. Nada foi
 * reescrito — a pessoa decide. Tooltip com `role="img"` + `aria-label` com a
 * explicação INTEIRA, nunca `title` nativo.
 */
export function MappingDivergenceIndicator({
  categoryCode,
  originDreCode,
}: {
  categoryCode: string;
  originDreCode: string | null | undefined;
}) {
  const explanation = originDreCode
    ? `A origem agora classifica esta categoria na conta ${originDreCode}, diferente da decisão vigente. Nada foi alterado: revise e decida.`
    : 'A origem deixou de declarar conta de demonstrativo para esta categoria. Nada foi alterado: revise e decida.';
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            role="img"
            tabIndex={0}
            aria-label={`Divergente da origem (categoria ${categoryCode}). ${explanation}`}
            className={cn(
              baseBadge,
              'bg-warning-muted text-warning ring-warning/30 focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2',
            )}
          >
            <AlertTriangle className="h-3 w-3" aria-hidden="true" />
            Divergente
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs text-xs leading-snug">
          {explanation}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
