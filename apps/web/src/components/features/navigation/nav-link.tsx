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

interface NavLinkProps {
  href: string;
  active: boolean;
  icon: React.ReactNode;
  children: React.ReactNode;
}

export function NavLink({ href, active, icon, children }: NavLinkProps) {
  return (
    <Link
      href={href}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'flex cursor-pointer items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors',
        'focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2',
        active ? 'bg-muted text-foreground font-medium' : 'text-muted-foreground hover:bg-muted',
      )}
    >
      {icon}
      <span>{children}</span>
    </Link>
  );
}
