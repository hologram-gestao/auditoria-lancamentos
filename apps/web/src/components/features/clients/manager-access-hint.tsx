'use client';

/**
 * "+N" discreto ao lado do responsável na lista de clientes (86e390m4c):
 * sinaliza que há mais gente com acesso sem carregar nomes em cada linha.
 *
 * Mesmo padrão de dica acessível do resto do produto (`author-label.tsx`):
 * `role="img"` + `aria-label` com a explicação INTEIRA (anunciada mesmo sem
 * abrir a dica) + `tabIndex={0}` com anel de foco — nunca `title` nativo.
 */
import type { MouseEvent } from 'react';

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

interface ManagerAccessHintProps {
  /** Pessoas com acesso ao cliente, responsável incluído (`manager_count`). */
  managerCount: number;
}

export function managerAccessLabel(extra: number): string {
  return extra === 1
    ? 'Mais 1 gerente com acesso a este cliente'
    : `Mais ${extra} gerentes com acesso a este cliente`;
}

export function ManagerAccessHint({ managerCount }: ManagerAccessHintProps) {
  const extra = managerCount - 1;
  if (extra <= 0) return null;
  const label = managerAccessLabel(extra);
  // A linha da lista navega no clique; tocar na dica não pode levar ao detalhe.
  const stop = (e: MouseEvent<HTMLSpanElement>) => e.stopPropagation();
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            role="img"
            tabIndex={0}
            aria-label={label}
            onClick={stop}
            className="bg-muted text-muted-foreground focus-visible:ring-ring ml-1.5 inline-flex cursor-default items-center rounded-full px-1.5 text-xs font-medium tabular-nums focus-visible:outline-none focus-visible:ring-2"
          >
            +{extra}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="text-xs leading-snug">
          {label}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
