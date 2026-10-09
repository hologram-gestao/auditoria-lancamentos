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
import type { AuthenticatedUser, ClientSummary } from '@/lib/contracts';

/** Tom do contador do item: info = em andamento, warning = decisão pendente, destructive = atraso. */
export type NavCountTone = 'info' | 'warning' | 'destructive';

export interface NavItem {
  href: string;
  label: string;
  icon: React.ReactNode;
  active: boolean;
  /**
   * Contador de pendência ao lado do rótulo (épico 86e3k1q1u). Sem `count`,
   * nada renderiza. Quem preenche é o resumo do cliente (subtask 4); o número
   * nunca vai sozinho: `countLabel` é o nome acessível e `countDetail` o texto do
   * tooltip, os dois com o detalhe inteiro ("12 títulos vencidos: 9 a receber · 3
   * a pagar").
   */
  count?: number;
  countTone?: NavCountTone;
  countLabel?: string;
  countDetail?: string;
}

export interface NavSection {
  heading?: string;
  items: NavItem[];
}

/** Rota da tela de Configurações do catálogo do de-para (86e3n70pn). */
const MAPPING_CATALOG_PATH = '/configuracoes/destinos-de-para';

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
  // A casa do tenant é a raiz do próprio cliente, que é a LISTA de conciliações
  // (de novo, desde 08/10/2026): o rótulo acompanha o destino (§7 Frontend).
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
  // organização vê Usuários, Categorias, Destinos do de-para e Layouts; a
  // plataforma vê todos; o gerente não vê a seção — e ela some inteira quando
  // nenhum item sobra, em vez de virar um cabeçalho órfão.
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
  // 86e3n70pn: o catálogo do de-para (destinos e alvos) é configuração da
  // ORGANIZAÇÃO — plataforma e admin. O gerente decide o de-para dos clientes da
  // carteira, mas não escreve no catálogo que vale para todos eles.
  {
    href: MAPPING_CATALOG_PATH,
    label: 'Destinos do de-para',
    icon: <ArrowRightLeft className="h-4 w-4" aria-hidden="true" />,
    permission: 'manage_mapping_catalog',
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

/**
 * A tela do catálogo com um destino já aberto (`?destino=<id>`, o parâmetro que
 * `mapping-catalog-screen.tsx` lê). É para onde o aviso de catálogo vazio do de-para
 * manda quem pode cadastrar os alvos.
 */
export function mappingCatalogPath(destinationId?: string): string {
  return destinationId
    ? `${MAPPING_CATALOG_PATH}?destino=${encodeURIComponent(destinationId)}`
    : MAPPING_CATALOG_PATH;
}

/**
 * Rota da LISTA de conciliações do cliente, que é a tela de entrada dele
 * (`/clientes/{id}`). Fonte única: quem manda para a lista (painel, detalhe após
 * excluir, processamento) chama esta função, e a rota muda num lugar só.
 */
export function reconciliationsPath(clientId: string): string {
  return `/clientes/${clientId}`;
}

/** Rota do PAINEL do cliente (86e3k1q54). Fonte única, como a da lista. */
export function dashboardPath(clientId: string): string {
  return `/clientes/${clientId}/painel`;
}

/** Rota da aba "Origem por arquivo" (S14) — a mesma que o link do de-para aponta. */
export function fileOriginPath(clientId: string, competence?: string | null): string {
  const base = `/clientes/${clientId}/origem-arquivo`;
  return competence ? `${base}?competence=${encodeURIComponent(competence)}` : base;
}

/**
 * Rota da tela "Categorias do Omie" (S10; "Plano de Contas" até 86e3n70pn). A rota
 * `/plano-de-contas` ficou: link salvo continua abrindo a mesma tela.
 */
export function chartOfAccountsPath(clientId: string): string {
  return `/clientes/${clientId}/plano-de-contas`;
}

/**
 * Rota da tela "Plano contábil" (S16) — o plano do sistema contábil de DESTINO,
 * distinto das "Categorias do Omie" da origem (S10). `section=conta-do-banco` leva
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

/**
 * O contador de UM item: número, tom, e o detalhe que diz do que ele é e de onde ele
 * vem. O detalhe é o tooltip e também o nome acessível da pílula (`countLabel`):
 * quem não vê o tooltip ouve a mesma frase.
 */
export interface NavCount {
  count: number;
  countTone: NavCountTone;
  countLabel: string;
  countDetail: string;
}

function plural(count: number, singular: string, pluralForm: string): string {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

function navCount(count: number, countTone: NavCountTone, countDetail: string): NavCount {
  return { count, countTone, countLabel: countDetail, countDetail };
}

/**
 * Os contadores do menu do cliente a partir do resumo (86e3k1q3x). Três itens
 * e só três têm contador, cada um na cor do badge que a tela de destino já usa:
 * Conciliações em `info` (em andamento: processando ou em revisão), Carteira em
 * `destructive` (títulos vencidos) e De-para em `warning` (categorias sem
 * decisão). Zero não é pendência: o item fica sem pílula. `titles` nulo (papel
 * que não lê a carteira) também.
 *
 * O De-para conta o MAIOR "sem decisão" entre os destinos, não a soma (decisão do
 * Pedro, 08/10/2026): a tela do de-para mostra um destino por vez, e a mesma
 * categoria sem decisão em cinco destinos somava cinco vezes (o menu dizia 1325
 * onde a tela dizia 265). O detalhe por destino vai no tooltip, do mais pendente
 * para o menos, com o nome do catálogo (`destinationNames`, tipo → nome) ou, sem
 * ele, o código.
 *
 * Nenhum número é calculado aqui além do máximo e da soma do que o servidor
 * contou: o mês, o "em andamento" e o "vencido" são decisões do `/summary`.
 */
export function clientNavCounts(
  summary: ClientSummary,
  destinationNames: ReadonlyMap<string, string> = new Map(),
): {
  reconciliations?: NavCount;
  titles?: NavCount;
  mapping?: NavCount;
} {
  const counts: { reconciliations?: NavCount; titles?: NavCount; mapping?: NavCount } = {};
  const { byStatus } = summary.reconciliations;
  const inProgress = byStatus.processing + byStatus.reviewing;
  if (inProgress > 0) {
    counts.reconciliations = navCount(
      inProgress,
      'info',
      `${inProgress} em andamento: ${byStatus.processing} em processamento · ${byStatus.reviewing} em revisão`,
    );
  }
  const titles = summary.titles;
  const overdue = titles?.overdueCount ?? 0;
  if (titles && overdue > 0) {
    counts.titles = navCount(
      overdue,
      'destructive',
      `${plural(overdue, 'título vencido', 'títulos vencidos')}: ${titles.aReceber.overdueCount} a receber · ${titles.aPagar.overdueCount} a pagar`,
    );
  }
  const pending = summary.mapping
    .filter((item) => item.withoutDecision > 0)
    // `sort` é estável: no empate, a ordem do servidor.
    .sort((a, b) => b.withoutDecision - a.withoutDecision);
  const largest = pending[0]?.withoutDecision ?? 0;
  if (largest > 0) {
    const perDestination = pending
      .map(
        (item) =>
          `${destinationNames.get(item.destinationCode) ?? item.destinationCode}: ${item.withoutDecision}`,
      )
      .join(', ');
    counts.mapping = navCount(
      largest,
      'warning',
      `${plural(largest, 'categoria sem decisão', 'categorias sem decisão')} · ${perDestination}`,
    );
  }
  return counts;
}

/**
 * Camada do CLIENTE: as seções internas de `/clientes/{id}/**`, agrupadas em
 * Operação, Cadastros e Acesso (86e3k1q2j). O gating de cada item não mudou com
 * o agrupamento; seção que fica sem item some inteira (a mesma regra de
 * "Configurações" na camada global), nunca um cabeçalho órfão.
 */
export function clientNavSections(
  user: AuthenticatedUser,
  clientId: string,
  pathname: string,
  summary?: ClientSummary | undefined,
  destinationNames?: ReadonlyMap<string, string>,
): NavSection[] {
  // Sem resumo (carregando, erro, sem acesso), nenhum contador: o menu nunca
  // espera o resumo para aparecer. Sem os nomes dos destinos, o detalhe do
  // De-para sai com o código.
  const counts = summary ? clientNavCounts(summary, destinationNames) : {};
  const base = `/clientes/${clientId}`;
  const accountsHref = `${base}/contas`;
  const reconciliationsHref = reconciliationsPath(clientId);
  const dashboardHref = dashboardPath(clientId);
  const usersHref = `${base}/usuarios`;
  const glossaryHref = `${base}/glossario`;
  const chartOfAccountsHref = chartOfAccountsPath(clientId);
  const accountingChartHref = accountingChartPath(clientId);
  const titlesHref = `${base}/carteira`;
  const mappingHref = `${base}/de-para`;
  const fileOriginHref = fileOriginPath(clientId);
  // Cada item casa pela PRÓPRIA rota (86e3k1q5n): não existe item ativo "por
  // exclusão". "Conciliações" é a raiz do cliente (a lista) e só ela, mais o
  // detalhe e o processamento de uma conciliação (`/conciliacao/{id}/**`), que
  // são a mesma área um nível abaixo; o Painel é `/painel`. Rota que nenhum item
  // reivindica fica sem item ativo, em vez de acender um item que não é dela.
  const isDashboard = isPathActive(pathname, dashboardHref);
  const isReconciliations =
    pathname === reconciliationsHref || pathname.startsWith(`${base}/conciliacao/`);
  const isAccounts = isPathActive(pathname, accountsHref);
  const isUsers = isPathActive(pathname, usersHref);
  const isGlossary = isPathActive(pathname, glossaryHref);
  const isChartOfAccounts = isPathActive(pathname, chartOfAccountsHref);
  const isAccountingChart = isPathActive(pathname, accountingChartHref);
  const isTitles = isPathActive(pathname, titlesHref);
  const isMapping = isPathActive(pathname, mappingHref);
  const isFileOrigin = isPathActive(pathname, fileOriginHref);

  // Operação: o trabalho do mês. A lista de conciliações é a tela de entrada do
  // cliente (`/clientes/{id}`, decisão do Pedro em 08/10/2026, depois do uso com
  // dado real); o Painel mora em `/painel` e continua o primeiro item da seção.
  const operation: NavItem[] = [
    {
      href: dashboardHref,
      label: 'Painel',
      icon: <LayoutDashboard className="h-4 w-4" aria-hidden="true" />,
      active: isDashboard,
    },
    {
      href: reconciliationsHref,
      label: 'Conciliações',
      icon: <ListChecks className="h-4 w-4" aria-hidden="true" />,
      active: isReconciliations,
      ...counts.reconciliations,
    },
  ];
  // S11 (R5): "Carteira" é montada pela MATRIZ. A célula de LER é ✅ nos cinco
  // papéis hoje, então na prática todo mundo com acesso ao cliente vê o item; o
  // que o gating garante é que, no dia em que a célula fechar para algum papel,
  // a rota e o item sumam JUNTOS. Quem pede permissão separada é SINCRONIZAR,
  // dentro da tela.
  if (hasPermission(user, 'view_client_receivables')) {
    operation.push({
      href: titlesHref,
      label: 'Carteira',
      icon: <Wallet className="h-4 w-4" aria-hidden="true" />,
      active: isTitles,
      ...counts.titles,
    });
  }
  // S12 (R6): "De-para" NÃO é gated, pela regra do Glossário: LER é de todo
  // papel que alcança o cliente, o operador inclusive, que vê a lista e a
  // prévia. O backend não declara permissão de leitura (a rota é
  // `AccessibleClientDep`), e inventar uma aqui esconderia o que o servidor
  // libera. Quem pede permissão é a ESCRITA (`manage_client_mapping`) e o
  // sincronizar (`sync_client_movements`), dentro da tela.
  operation.push({
    href: mappingHref,
    label: 'De-para',
    icon: <ArrowRightLeft className="h-4 w-4" aria-hidden="true" />,
    active: isMapping,
    ...counts.mapping,
  });
  // S14 (R5), revisto no follow-up 86e3fqnc9: "Origem por arquivo" é SEMPRE
  // listada. Antes ela só existia para o cliente que já tinha conexão
  // `arquivo`, e o resultado era um recurso invisível: quem opera não descobria
  // que dá para atender cliente sem ERP mandando a planilha do mês. Quem
  // explica o estado (sem origem, com Omie, encerrado) é a TELA, que também
  // decide a ação pela permissão. Esconder aqui não é regra de §4.9: LER a aba
  // não pede permissão nenhuma (a rota é `AccessibleClientDep`), e o que o
  // servidor negaria é CONECTAR, que a tela já esconde de quem não pode.
  operation.push({
    href: fileOriginHref,
    label: 'Origem por arquivo',
    icon: <FileSpreadsheet className="h-4 w-4" aria-hidden="true" />,
    active: isFileOrigin,
  });

  // Cadastros: o que o mês consulta e quase nunca muda.
  const registry: NavItem[] = [
    {
      href: accountsHref,
      label: 'Contas Bancárias',
      icon: <Landmark className="h-4 w-4" aria-hidden="true" />,
      active: isAccounts,
    },
    // Glossário (S6/R2) NÃO é gated: ler é de todo papel com acesso ao cliente;
    // o operador o usa como referência na revisão. Quem pede permissão é a
    // ESCRITA, dentro da tela.
    {
      href: glossaryHref,
      label: 'Glossário',
      icon: <BookOpen className="h-4 w-4" aria-hidden="true" />,
      active: isGlossary,
    },
  ];
  // S10 (R4): "Categorias do Omie" pela MESMA regra da Carteira: a célula de LER é
  // ✅ nos cinco papéis hoje, e o gating faz rota e item sumirem JUNTOS no dia
  // em que ela fechar. Quem pede permissão separada é SINCRONIZAR, dentro da tela.
  if (hasPermission(user, 'view_client_chart_of_accounts')) {
    registry.push({
      href: chartOfAccountsHref,
      // "Plano de Contas" até 09/10/2026 (86e3n70pn): ao lado de "Plano contábil" o
      // escritório confundia as duas. A tela é a das CATEGORIAS que o Omie declara.
      label: 'Categorias do Omie',
      icon: <ListTree className="h-4 w-4" aria-hidden="true" />,
      active: isChartOfAccounts,
    });
  }
  // S16 (R1): "Plano contábil", o plano do sistema contábil de DESTINO, ao lado
  // das "Categorias do Omie" da origem (nomes distintos de propósito). NÃO é gated,
  // pela regra do De-para: a LEITURA é `AccessibleClientDep` no backend, sem
  // permissão própria. Quem pede permissão (`manage_client_accounting_chart`) é
  // importar e associar a conta do banco, dentro da tela.
  registry.push({
    href: accountingChartHref,
    label: 'Plano contábil',
    icon: <Calculator className="h-4 w-4" aria-hidden="true" />,
    active: isAccountingChart,
  });

  // Acesso. Matriz: "Usuários" é de quem gere as pessoas DO tenant: gerente do
  // cliente, admin, plataforma e, desde a D2 (86e36ecjp), o gerente da
  // organização nos clientes da CARTEIRA. O "da carteira" não é esta linha: é
  // `resolve_client_access`, no servidor, que já decide se ele chega no cliente.
  // O operador do cliente segue de fora, e para ele a seção some inteira.
  const access: NavItem[] = [];
  if (hasPermission(user, 'manage_client_users')) {
    access.push({
      href: usersHref,
      label: 'Usuários',
      icon: <UserCog className="h-4 w-4" aria-hidden="true" />,
      active: isUsers,
    });
  }

  const sections: NavSection[] = [
    { heading: 'Operação', items: operation },
    { heading: 'Cadastros', items: registry },
    { heading: 'Acesso', items: access },
  ];
  return sections.filter((section) => section.items.length > 0);
}
