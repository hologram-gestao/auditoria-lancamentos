/**
 * Árvore de navegação — fonte ÚNICA (86e2n39h7 / épico 86e2n4tbx).
 *
 * Armadilha registrada no épico: a árvore não pode ser escrita duas vezes.
 * O sidebar do shell (`SidebarNav`), os chips mobile do `ClientShell` e o
 * futuro drawer mobile (86e2n4pf9) consomem ESTES builders — item novo entra
 * aqui e aparece em todos os consumidores, com o mesmo gating.
 *
 * Gating pela matriz de `lib/authz` (§4.9) — nunca `role === '...'` solto.
 */
import {
  AlertTriangle,
  BookOpen,
  Landmark,
  LayoutDashboard,
  ListChecks,
  Settings,
  UserCog,
  Users as UsersIcon,
} from 'lucide-react';

import { canManageSystemUsers, hasPermission, homePathFor, isClientScoped } from '@/lib/authz';
import type { AuthenticatedUser } from '@/lib/contracts';

export interface NavItem {
  href: string;
  label: string;
  icon: React.ReactNode;
  active: boolean;
}

export interface NavSection {
  heading?: string;
  items: NavItem[];
}

/** Ativo quando a rota é o próprio href ou desce dele (`/x` cobre `/x/y`). */
function isPathActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * Extrai o `clientId` de rotas `/clientes/{id}/**`. A lista (`/clientes`) não
 * casa — e qualquer segundo segmento É um id: não existe rota estática irmã de
 * `[clientId]` na árvore do App Router (conferido em 23/08/2026).
 */
export function clientIdFromPathname(pathname: string): string | null {
  const match = /^\/clientes\/([^/]+)(?:\/|$)/.exec(pathname);
  return match?.[1] ?? null;
}

/** Camada GLOBAL: lista de clientes (ou a casa do tenant) + Configurações. */
export function globalNavSections(user: AuthenticatedUser, pathname: string): NavSection[] {
  // Gating por perfil (R4): usuário DE tenant não tem lista global de clientes —
  // a casa dele é o próprio cliente. Mostrar "Clientes" para ele seria oferecer
  // uma rota que o servidor nega.
  const home = homePathFor(user);
  const main: NavItem[] = isClientScoped(user)
    ? [
        {
          href: home,
          label: 'Conciliações',
          icon: <ListChecks className="h-4 w-4" aria-hidden="true" />,
          active: isPathActive(pathname, home),
        },
      ]
    : [
        {
          href: '/clientes',
          label: 'Clientes',
          icon: <UsersIcon className="h-4 w-4" aria-hidden="true" />,
          active: isPathActive(pathname, '/clientes'),
        },
      ];

  const sections: NavSection[] = [{ items: main }];
  if (canManageSystemUsers(user)) {
    sections.push({
      heading: 'Configurações',
      items: [
        {
          href: '/configuracoes/usuarios',
          label: 'Usuários',
          icon: <Settings className="h-4 w-4" aria-hidden="true" />,
          active: isPathActive(pathname, '/configuracoes/usuarios'),
        },
        {
          href: '/configuracoes/anomalias',
          label: 'Tipos de Anomalia',
          icon: <AlertTriangle className="h-4 w-4" aria-hidden="true" />,
          active: isPathActive(pathname, '/configuracoes/anomalias'),
        },
      ],
    });
  }
  return sections;
}

/** Camada do CLIENTE: as seções internas de `/clientes/{id}/**`. */
export function clientNavItems(
  user: AuthenticatedUser,
  clientId: string,
  pathname: string,
): NavItem[] {
  const base = `/clientes/${clientId}`;
  const accountsHref = `${base}/contas`;
  const dashboardHref = `${base}/painel`;
  const usersHref = `${base}/usuarios`;
  const glossaryHref = `${base}/glossario`;
  // "Conciliações" continua ativo dentro do detalhe de uma conciliação — é a
  // mesma área de navegação, só que um nível abaixo (regra herdada do
  // ClientShell, que era o dono desta árvore até a 86e2n39h7).
  const isAccounts = pathname.startsWith(accountsHref);
  const isDashboard = pathname.startsWith(dashboardHref);
  const isUsers = pathname.startsWith(usersHref);
  const isGlossary = pathname.startsWith(glossaryHref);
  const isReconciliations = !isAccounts && !isDashboard && !isUsers && !isGlossary;

  const items: NavItem[] = [
    {
      href: base,
      label: 'Conciliações',
      icon: <ListChecks className="h-4 w-4" aria-hidden="true" />,
      active: isReconciliations,
    },
    {
      href: accountsHref,
      label: 'Contas Bancárias',
      icon: <Landmark className="h-4 w-4" aria-hidden="true" />,
      active: isAccounts,
    },
    {
      href: dashboardHref,
      label: 'Painel',
      icon: <LayoutDashboard className="h-4 w-4" aria-hidden="true" />,
      active: isDashboard,
    },
    // Glossário (S6/R2) NÃO é gated: ler é de todo papel com acesso ao cliente —
    // o operador o usa como referência na revisão. Quem pede permissão é a
    // ESCRITA, dentro da tela.
    {
      href: glossaryHref,
      label: 'Glossário',
      icon: <BookOpen className="h-4 w-4" aria-hidden="true" />,
      active: isGlossary,
    },
  ];
  // Matriz do R4: "Usuários" é do gerente do cliente e do admin do sistema. O
  // gerente do sistema opera a carteira, mas não administra as pessoas de
  // dentro do tenant.
  if (hasPermission(user, 'manage_client_users')) {
    items.push({
      href: usersHref,
      label: 'Usuários',
      icon: <UserCog className="h-4 w-4" aria-hidden="true" />,
      active: isUsers,
    });
  }
  return items;
}
