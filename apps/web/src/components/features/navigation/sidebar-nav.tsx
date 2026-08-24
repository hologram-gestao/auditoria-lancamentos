'use client';

/**
 * Navegação do `<aside>` do shell — em CAMADAS (86e2n39h7).
 *
 * Dentro de `/clientes/{id}/**` o menu do cliente OCUPA o lugar do menu
 * principal: "Voltar para clientes" no topo (só equipe Hologram — usuário de
 * tenant não tem camada acima), nome do cliente, e as seções internas. Fora
 * desse contexto, a camada global (Clientes/Conciliações + Configurações).
 *
 * Decisões de 23/08/2026 (task 86e2n39h7):
 *   - "Voltar" NAVEGA para `/clientes` — a camada exibida deriva 100% do
 *     pathname, sem estado próprio no shell. Previsível e testável.
 *   - O nome do cliente aparece SEMPRE (para tenant é a própria empresa).
 *   - O breadcrumb do `ClientShell` continua: ele mostra PROFUNDIDADE, o
 *     Voltar troca de CAMADA.
 *
 * O nome vem do MESMO `useClientDetail` do `ClientShell` — o TanStack serve do
 * cache, sem segundo request. Acesso negado no front (`canAccessClient`)
 * degrada para a camada global: não oferecemos rotas que o servidor nega
 * (§4.9); o conteúdo (`AccessDenied`) é quem explica.
 *
 * O `aria-label` "Seções do cliente" é o MESMO dos chips mobile do
 * `ClientShell` de propósito: só um dos dois está visível por breakpoint
 * (aside é `hidden md:block`, chips são `md:hidden`), então a árvore de
 * acessibilidade nunca tem os dois — e os testes e2e localizam "a navegação
 * do cliente visível" com um seletor só, nos dois viewports.
 */
import { ArrowLeft } from 'lucide-react';
import { usePathname } from 'next/navigation';
import { Fragment } from 'react';

import { useClientDetail } from '@/hooks/use-clients';
import { canAccessClient, canSeeSystemArea } from '@/lib/authz';
import type { AuthenticatedUser } from '@/lib/contracts';
import { cn } from '@/lib/utils';

import { clientIdFromPathname, clientNavItems, globalNavSections } from './nav-items';
import { NavLink } from './nav-link';

interface SidebarNavProps {
  user: AuthenticatedUser;
  /**
   * Disparado no clique de QUALQUER link do menu (Voltar incluso). O drawer
   * mobile (86e2n4pf9) usa para fechar imediatamente — inclusive no clique do
   * item da página atual, onde o pathname não muda.
   */
  onNavigate?: () => void;
}

export function SidebarNav({ user, onNavigate }: SidebarNavProps) {
  const pathname = usePathname();
  const clientId = clientIdFromPathname(pathname);
  const canAccess = clientId !== null && canAccessClient(user, clientId);
  // Hook incondicional (rules of hooks); `enabled` barra o id vazio e o deep
  // link de outro tenant — nem dispara request (mesma regra do ClientShell).
  const detailQuery = useClientDetail(clientId ?? '', { enabled: canAccess });

  if (clientId === null || !canAccess) {
    return (
      <nav aria-label="Navegação principal" className="flex flex-col gap-1">
        {globalNavSections(user, pathname).map((section) => (
          <Fragment key={section.heading ?? 'principal'}>
            {section.heading !== undefined && (
              <div className="text-muted-foreground mt-4 px-3 pb-1 text-xs font-medium uppercase tracking-wide">
                {section.heading}
              </div>
            )}
            {section.items.map((item) => (
              <NavLink
                key={item.href}
                href={item.href}
                active={item.active}
                icon={item.icon}
                onClick={onNavigate}
              >
                {item.label}
              </NavLink>
            ))}
          </Fragment>
        ))}
      </nav>
    );
  }

  const showBack = canSeeSystemArea(user);
  // Nome indisponível (erro na carga) esconde o bloco inteiro — um rótulo
  // "Cliente" órfão leria como defeito; o menu continua funcional e o Voltar
  // é a rota de escape.
  const clientName = detailQuery.data?.name;

  return (
    <nav aria-label="Seções do cliente" className="flex flex-col gap-1">
      {showBack && (
        <NavLink
          href="/clientes"
          active={false}
          icon={<ArrowLeft className="h-4 w-4" aria-hidden="true" />}
          onClick={onNavigate}
        >
          Voltar para clientes
        </NavLink>
      )}
      {!detailQuery.isError && (
        <div className={cn('px-3 pb-2', showBack && 'mt-3')}>
          <div className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
            Cliente
          </div>
          {clientName !== undefined ? (
            <div className="truncate text-sm font-semibold">{clientName}</div>
          ) : (
            <div className="bg-muted mt-1 h-4 w-28 animate-pulse rounded" aria-hidden="true" />
          )}
        </div>
      )}
      {clientNavItems(user, clientId, pathname).map((item) => (
        <NavLink
          key={item.href}
          href={item.href}
          active={item.active}
          icon={item.icon}
          onClick={onNavigate}
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}
