/**
 * Link de navegação — visual ÚNICO para o sidebar do shell, os chips do
 * `ClientShell` e o futuro drawer mobile (86e2n4pf9). Unifica o `SidebarLink`
 * e o `ClientNavLink` que existiam duplicados com as mesmas classes.
 *
 * `aria-current="page"` no item ativo é critério de aceite da 86e2n39h7; o
 * anel de foco alinha com o restante da UI (shadcn padrão) — sem ele o Tab
 * caía no outline default do navegador.
 */
import Link from 'next/link';

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

import type { NavCountTone } from './nav-items';

/**
 * Pílula do contador: fundo `-muted` com o texto no token SÓLIDO (o par dos
 * badges, travado no theme-contrast). Classes literais por tom, para o
 * Tailwind enxergá-las na varredura.
 */
const COUNT_TONE_CLASS: Record<NavCountTone, string> = {
  info: 'bg-info-muted text-info',
  warning: 'bg-warning-muted text-warning',
  destructive: 'bg-destructive-muted text-destructive',
};

interface NavLinkProps {
  href: string;
  active: boolean;
  icon: React.ReactNode;
  children: React.ReactNode;
  /** Quem hospeda o menu num overlay (drawer mobile) fecha no clique. */
  onClick?: () => void;
  /** Contador de pendência; sem ele nada renderiza (épico 86e3k1q1u). */
  count?: number;
  countTone?: NavCountTone;
  /**
   * Nome acessível do contador ("12 títulos vencidos"). Obrigatório para ele
   * aparecer: o número sozinho não diz do que é, e sem rótulo o contador não
   * renderiza.
   */
  countLabel?: string;
  /**
   * Texto do tooltip do contador (o detalhe: "12 títulos vencidos: 9 a receber ·
   * 3 a pagar"). Sem ele, o tooltip mostra o `countLabel`.
   */
  countDetail?: string;
}

export function NavLink({
  href,
  active,
  icon,
  children,
  onClick,
  count,
  countTone = 'info',
  countLabel,
  countDetail,
}: NavLinkProps) {
  const showCount = count !== undefined && countLabel !== undefined && countLabel !== '';
  const link = (
    <Link
      href={href}
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'relative flex cursor-pointer items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors duration-150',
        'focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2',
        // `accent` (tint do marinho, 86e2ukrc9): item ativo e hover vestem a
        // marca — par accent-foreground/accent travado no theme-contrast.
        // Item ativo (86e3h57a5): barra de 3 px à esquerda na cor de ação
        // (`--primary`: o verde no Hologram) e um brilho que nasce na borda e
        // morre antes do ícone (12 px de padding). Os dois ficam FORA do texto:
        // nenhum par de contraste novo; o e2e mede o rótulo por pixel.
        active
          ? "bg-accent text-accent-foreground before:bg-primary font-medium shadow-[inset_10px_0_10px_-10px_hsl(var(--primary)/0.12)] before:absolute before:inset-y-1.5 before:left-0 before:w-[3px] before:rounded-full before:content-['']"
          : 'text-muted-foreground hover:bg-accent',
      )}
    >
      {icon}
      <span>{children}</span>
      {showCount && (
        <span
          role="img"
          aria-label={countLabel}
          className={cn(
            'ml-auto rounded-full px-2 py-0.5 text-[11px] font-semibold tabular-nums leading-none',
            COUNT_TONE_CLASS[countTone],
          )}
        >
          {count}
        </span>
      )}
    </Link>
  );
  if (!showCount) return link;
  // Com contador, o LINK inteiro é o gatilho do tooltip (86e3h57a5, ajuste de
  // 08/10/2026): ele já é focável, então o detalhe abre no hover e no foco do
  // teclado sem `tabIndex` na pílula (um focável dentro de outro). A pílula segue
  // `role="img"` com o mesmo texto no nome acessível; no toque, quem lê é ele.
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>{link}</TooltipTrigger>
        <TooltipContent side="right">{countDetail ?? countLabel}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
