'use client';

/**
 * Totalizador recolhível (86e3fr9qz, feedback do Lucas em 28/09/2026).
 *
 * A moldura comum dos cards de totais da carteira, do de-para e do plano de
 * contas: aberto, mostra os cards e, embaixo, a nota de apoio com o botão
 * "Ocultar totais"; recolhido, uma linha só com os números principais e o botão
 * "Mostrar totais". Assim a lista sobe sem que o essencial suma da tela.
 *
 * O estado é por TELA (`storageKey` próprio) e fica no `localStorage` deste
 * navegador: é conveniência de quem usa, não dado. Leitura e escrita vivem em
 * try/catch (janela privada, armazenamento bloqueado); sem armazenamento, abre
 * aberto. A leitura acontece depois de montar, para o HTML do servidor e o
 * primeiro render do cliente serem iguais (sem aviso de hidratação).
 *
 * O conteúdo recolhido continua montado com `hidden`: os botões que filtram
 * saem da ordem de tabulação e o `aria-controls` aponta sempre para um id que
 * existe.
 *
 * Os cards de dentro são `Card variant="elevated"` (quem chama), e o bloco entra
 * pelo `Reveal` (86e3h57a5): uma vez, parado sob movimento reduzido.
 */

import { ChevronDown, ChevronUp } from 'lucide-react';
import { useEffect, useId, useState } from 'react';

import { Reveal } from '@/components/shared/reveal';
import { Button } from '@/components/ui/button';

const STORAGE_PREFIX = 'adl:totais-recolhidos:';

function readCollapsed(storageKey: string): boolean {
  try {
    return window.localStorage.getItem(STORAGE_PREFIX + storageKey) === '1';
  } catch {
    return false;
  }
}

function writeCollapsed(storageKey: string, collapsed: boolean): void {
  try {
    if (collapsed) {
      window.localStorage.setItem(STORAGE_PREFIX + storageKey, '1');
    } else {
      window.localStorage.removeItem(STORAGE_PREFIX + storageKey);
    }
  } catch {
    // Sem armazenamento a escolha vale só até recarregar a página.
  }
}

interface CollapsibleSummaryProps {
  /** Chave própria da tela (ex.: `carteira`): cada tela lembra o seu estado. */
  storageKey: string;
  /** A linha única mostrada quando recolhido: os números principais. */
  collapsed: React.ReactNode;
  /** A nota de apoio mostrada embaixo dos cards quando aberto. */
  footnote: React.ReactNode;
  /** Os cards de totais. */
  children: React.ReactNode;
}

export function CollapsibleSummary({
  storageKey,
  collapsed,
  footnote,
  children,
}: CollapsibleSummaryProps) {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const contentId = useId();

  useEffect(() => {
    setIsCollapsed(readCollapsed(storageKey));
  }, [storageKey]);

  function toggle() {
    const next = !isCollapsed;
    setIsCollapsed(next);
    writeCollapsed(storageKey, next);
  }

  return (
    <div className="space-y-2">
      {/* Os cards entram uma vez, ao montar ou ao reabrir, pelo primitivo (86e3h57a5). */}
      <Reveal id={contentId} hidden={isCollapsed}>
        {children}
      </Reveal>
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        {isCollapsed ? (
          <div className="min-w-0 text-sm" data-testid="summary-collapsed">
            {collapsed}
          </div>
        ) : (
          <div className="text-muted-foreground min-w-0 text-xs">{footnote}</div>
        )}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 shrink-0 px-2 text-xs"
          aria-expanded={!isCollapsed}
          aria-controls={contentId}
          onClick={toggle}
        >
          {isCollapsed ? (
            <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {isCollapsed ? 'Mostrar totais' : 'Ocultar totais'}
        </Button>
      </div>
    </div>
  );
}

/**
 * A linha recolhida no formato comum às três telas: "valor rótulo · valor
 * rótulo". Cada par não quebra por dentro (valor monetário que quebra depois do
 * hífen vira outro número, defeito da S7); a linha quebra ENTRE os pares.
 */
export function SummaryInline({
  items,
}: {
  items: readonly { key: string; value: React.ReactNode; label: string }[];
}) {
  return (
    <p className="flex flex-wrap gap-x-1 gap-y-0.5">
      {items.map((item, index) => (
        <span key={item.key} className="whitespace-nowrap">
          <span className="font-semibold tabular-nums">{item.value}</span> {item.label}
          {index < items.length - 1 && (
            <span className="text-muted-foreground" aria-hidden="true">
              {' · '}
            </span>
          )}
        </span>
      ))}
    </p>
  );
}
