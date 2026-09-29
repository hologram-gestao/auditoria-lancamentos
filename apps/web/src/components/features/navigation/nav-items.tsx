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
  ArrowRightLeft,
  BookOpen,
  Building2,
  Calculator,
  FileOutput,
  FileSpreadsheet,
  Landmark,
  LayoutDashboard,
  ListChecks,
  ListTree,
  Settings,
  Tags,
  UserCog,
  Users as UsersIcon,
  Wallet,
} from 'lucide-react';

import { hasPermission, homePathFor, isClientScoped, type Permission } from '@/lib/authz';
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

  // Configurações item a item pela MATRIZ (86e36ecwa): com organizações, o
  // "quem vê Configurações" deixou de ser uma pergunta só. O admin da
  // organização vê as três primeiras; a plataforma vê as quatro; o gerente não
  // vê a seção — e ela some inteira quando nenhum item sobra, em vez de virar
  // um cabeçalho órfão.
  const settings = SETTINGS_ITEMS.filter((item) => hasPermission(user, item.permission)).map(
    (item) => ({
      href: item.href,
      label: item.label,
      icon: item.icon,
      active: isPathActive(pathname, item.href),
    }),
  );
  if (settings.length > 0) {
    sections.push({ heading: 'Configurações', items: settings });
  }
  return sections;
}

/**
 * Os itens de Configurações e a permissão que libera cada um — a lista existe
 * separada para que "item novo" seja uma linha aqui, nunca um `if` a mais.
 */
const SETTINGS_ITEMS: ReadonlyArray<{
  href: string;
  label: string;
  icon: React.ReactNode;
  permission: Permission;
}> = [
  {
    href: '/configuracoes/usuarios',
    label: 'Usuários',
    icon: <Settings className="h-4 w-4" aria-hidden="true" />,
    permission: 'manage_org_users',
  },
  {
    href: '/configuracoes/anomalias',
    label: 'Tipos de Anomalia',
    icon: <AlertTriangle className="h-4 w-4" aria-hidden="true" />,
    permission: 'manage_anomaly_types',
  },
  {
    href: '/configuracoes/categorias',
    label: 'Categorias de Cliente',
    icon: <Tags className="h-4 w-4" aria-hidden="true" />,
    permission: 'manage_client_categories',
  },
  // S13 (R3): os layouts do arquivo contábil são configuração da ORGANIZAÇÃO —
  // plataforma e admin. O gerente GERA o arquivo (`generate_accounting_file`, no
  // de-para) mas não administra layout, então não vê o item.
  {
    href: '/configuracoes/layouts-exportacao',
    label: 'Layouts de exportação',
    icon: <FileOutput className="h-4 w-4" aria-hidden="true" />,
    permission: 'manage_export_layouts',
  },
  {
    href: '/configuracoes/organizacoes',
    label: 'Organizações',
    icon: <Building2 className="h-4 w-4" aria-hidden="true" />,
    permission: 'manage_platform',
  },
];

/** Rota da aba "Origem por arquivo" (S14) — a mesma que o link do de-para aponta. */
export function fileOriginPath(clientId: string, competence?: string | null): string {
  const base = `/clientes/${clientId}/origem-arquivo`;
  return competence ? `${base}?competence=${encodeURIComponent(competence)}` : base;
}

/**
 * Rota da tela "Plano contábil" (S16) — o plano do sistema contábil de DESTINO,
 * distinto do "Plano de Contas" da origem (S10). `section=conta-do-banco` leva
 * direto à seção da conta do banco: é para onde o de-para manda quando a
 * materialização em `conta_contabil` fica sem um lado da partida.
 */
export function accountingChartPath(clientId: string, section?: 'conta-do-banco'): string {
  const base = `/clientes/${clientId}/plano-contabil`;
  return section ? `${base}#${section}` : base;
}

/**
 * A prévia do de-para de UMA competência (S12: `view=previa` + `competence`,
 * os dois parâmetros que `client-mapping-screen.tsx` lê da URL). É para onde o
 * envio do arquivo (S14) manda depois de processar — a competência sozinha não
 * bastaria: ela só age na aba da prévia.
 */
export function mappingPreviewPath(clientId: string, competence: string): string {
  return `/clientes/${clientId}/de-para?view=previa&competence=${encodeURIComponent(competence)}`;
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
  const chartOfAccountsHref = `${base}/plano-de-contas`;
  const accountingChartHref = accountingChartPath(clientId);
  const titlesHref = `${base}/carteira`;
  const mappingHref = `${base}/de-para`;
  const fileOriginHref = fileOriginPath(clientId);
  // "Conciliações" continua ativo dentro do detalhe de uma conciliação — é a
  // mesma área de navegação, só que um nível abaixo (regra herdada do
  // ClientShell, que era o dono desta árvore até a 86e2n39h7).
  //
  // ⚠️ Rota nova do cliente entra TAMBÉM nesta negação: "Conciliações" é o
  // fallback, então esquecer a linha aqui deixa dois itens marcados como ativos
  // ao mesmo tempo.
  const isAccounts = pathname.startsWith(accountsHref);
  const isDashboard = pathname.startsWith(dashboardHref);
  const isUsers = pathname.startsWith(usersHref);
  const isGlossary = pathname.startsWith(glossaryHref);
  const isChartOfAccounts = pathname.startsWith(chartOfAccountsHref);
  const isAccountingChart = pathname.startsWith(accountingChartHref);
  const isTitles = pathname.startsWith(titlesHref);
  const isMapping = pathname.startsWith(mappingHref);
  const isFileOrigin = pathname.startsWith(fileOriginHref);
  const isReconciliations =
    !isAccounts &&
    !isDashboard &&
    !isUsers &&
    !isGlossary &&
    !isChartOfAccounts &&
    !isAccountingChart &&
    !isTitles &&
    !isMapping &&
    !isFileOrigin;

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
  // S10 (R4): "Plano de Contas" é montado pela MATRIZ, como os itens de
  // Configurações. A célula de LER é ✅ nos cinco papéis hoje, então na prática
  // todo mundo com acesso ao cliente vê o item — o que o gating garante é que,
  // no dia em que a célula fechar para algum papel, a rota e o item sumam
  // JUNTOS. Quem pede permissão separada é SINCRONIZAR, dentro da tela.
  if (hasPermission(user, 'view_client_chart_of_accounts')) {
    items.push({
      href: chartOfAccountsHref,
      label: 'Plano de Contas',
      icon: <ListTree className="h-4 w-4" aria-hidden="true" />,
      active: isChartOfAccounts,
    });
  }
  // S16 (R1): "Plano contábil" — o plano do sistema contábil de DESTINO, ao lado
  // do "Plano de Contas" da origem (nomes distintos de propósito). NÃO é gated,
  // pela regra do De-para: a LEITURA é `AccessibleClientDep` no backend, sem
  // permissão própria. Quem pede permissão (`manage_client_accounting_chart`) é
  // importar e associar a conta do banco, dentro da tela.
  items.push({
    href: accountingChartHref,
    label: 'Plano contábil',
    icon: <Calculator className="h-4 w-4" aria-hidden="true" />,
    active: isAccountingChart,
  });
  // S11 (R5): "Carteira" pela MESMA regra do Plano de Contas. A célula de LER é
  // ✅ nos cinco papéis hoje, então na prática todo mundo com acesso ao cliente
  // vê o item — o que o gating garante é que, no dia em que a célula fechar
  // para algum papel, a rota e o item sumam JUNTOS. Quem pede permissão
  // separada é SINCRONIZAR, dentro da tela.
  if (hasPermission(user, 'view_client_receivables')) {
    items.push({
      href: titlesHref,
      label: 'Carteira',
      icon: <Wallet className="h-4 w-4" aria-hidden="true" />,
      active: isTitles,
    });
  }
  // S12 (R6): "De-para" NÃO é gated, pela regra do Glossário: LER é de todo
  // papel que alcança o cliente — o operador inclusive, que vê a lista e a
  // prévia. O backend não declara permissão de leitura (a rota é
  // `AccessibleClientDep`), e inventar uma aqui esconderia o que o servidor
  // libera. Quem pede permissão é a ESCRITA (`manage_client_mapping`) e o
  // sincronizar (`sync_client_movements`), dentro da tela.
  items.push({
    href: mappingHref,
    label: 'De-para',
    icon: <ArrowRightLeft className="h-4 w-4" aria-hidden="true" />,
    active: isMapping,
  });
  // S14 (R5), revisto no follow-up 86e3fqnc9: "Origem por arquivo" é SEMPRE
  // listada. Antes ela só existia para o cliente que já tinha conexão
  // `arquivo`, e o resultado era um recurso invisível: quem opera não descobria
  // que dá para atender cliente sem ERP mandando a planilha do mês. Quem
  // explica o estado — sem origem, com Omie, encerrado — é a TELA, que também
  // decide a ação pela permissão. Esconder aqui não é regra de §4.9: LER a aba
  // não pede permissão nenhuma (a rota é `AccessibleClientDep`), e o que o
  // servidor negaria é CONECTAR, que a tela já esconde de quem não pode.
  items.push({
    href: fileOriginHref,
    label: 'Origem por arquivo',
    icon: <FileSpreadsheet className="h-4 w-4" aria-hidden="true" />,
    active: isFileOrigin,
  });
  // Matriz: "Usuários" é de quem gere as pessoas DO tenant — gerente do
  // cliente, admin, plataforma e, desde a D2 (86e36ecjp), o gerente da
  // organização nos clientes da CARTEIRA. O "da carteira" não é esta linha: é
  // `resolve_client_access`, no servidor, que já decide se ele chega no cliente.
  // O operador do cliente segue de fora.
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
