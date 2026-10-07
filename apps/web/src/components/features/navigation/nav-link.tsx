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
}: NavLinkProps) {
  const showCount = count !== undefined && countLabel !== undefined && countLabel !== '';
  return (
    <Link
      href={href}
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'flex cursor-pointer items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors',
        'focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2',
        // `accent` (tint do marinho, 86e2ukrc9): item ativo e hover vestem a
        // marca — par accent-foreground/accent travado no theme-contrast.
        active
          ? 'bg-accent text-accent-foreground font-medium'
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
}
