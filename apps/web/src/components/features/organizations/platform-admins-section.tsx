'use client';

/**
 * Quem administra a PLATAFORMA — seção só-leitura da tela de Organizações.
 *
 * Por que existe: até aqui, ninguém enxergava os `platform_admin`. O
 * `GET /users` filtra `scope='system'` no próprio SELECT (anti-IDOR da
 * 86e36ecar: o admin de uma organização não pode alcançar a conta da
 * plataforma) e o `users_count` de cada organização conta só o staff dela —
 * usuário de plataforma tem `organization_id` nulo e fica de fora de todo
 * total. O efeito colateral que ninguém decidiu foi a plataforma também não
 * ver os pares dela, e depois da promoção das contas reais essas pessoas
 * simplesmente somem da tela de Usuários.
 *
 * Por que aqui e não como filtro em Usuários: promover e despromover é só pelo
 * script (`promote_platform_admin.py`, decisão Q3), e `PATCH /users/{id}`
 * responde 404 para uma linha de plataforma. Dentro da tabela de Usuários, as
 * ações da linha e o botão de criar teriam de ser escondidos caso a caso —
 * ação na tela que o servidor nega é defeito (§4.9). Numa seção só-leitura,
 * isso é verdade por construção.
 *
 * A altura é LIMITADA (`max-h-44`) de propósito: a tabela de organizações
 * acima é `flex-1`, então uma seção que crescesse com o conteúdo iria comendo
 * o espaço dela. Passando do teto, a `ScrollRegion` rola — e é ela que dá o
 * `tabIndex`/`role`/`aria-label` que o `scrollable-region-focusable` exige.
 */

import { UserStatusBadge } from '@/components/features/users/user-badges';
import { ScrollRegion } from '@/components/ui/scroll-region';
import { usePlatformAdminsList } from '@/hooks/use-organizations';

const TITULO = 'Administradores da plataforma';
// O nome da região rolável é DIFERENTE do título da seção de propósito: a
// `<section aria-labelledby>` já é um landmark com o texto do `<h2>`, e uma
// região aninhada com o MESMO nome dá dois landmarks homônimos — confuso no
// leitor de tela, e ambíguo para qualquer locator por papel e nome. Mesmo
// desencontro deliberado de `client-managers-section` ("Gerentes com acesso"
// contra "Gerentes com acesso ao cliente").
const NOME_DA_REGIAO = 'Lista de administradores da plataforma';

export function PlatformAdminsSection() {
  const { data, isLoading, isError } = usePlatformAdminsList();
  const rows = data ?? [];

  return (
    <section aria-labelledby="platform-admins-heading" className="border-border rounded-lg border">
      <div className="border-border border-b px-4 py-3">
        <h2 id="platform-admins-heading" className="text-sm font-semibold">
          {TITULO}
        </h2>
        <p className="text-muted-foreground text-xs">
          Alcançam todas as organizações e não pertencem a nenhuma. Entram e saem apenas pelo script
          de promoção, nunca por esta tela.
        </p>
      </div>

      <ScrollRegion label={NOME_DA_REGIAO} className="max-h-44">
        {isLoading ? (
          <p className="text-muted-foreground px-4 py-3 text-sm">Carregando...</p>
        ) : isError ? (
          <p className="text-destructive px-4 py-3 text-sm">
            Não foi possível carregar os administradores da plataforma.
          </p>
        ) : rows.length === 0 ? (
          <p className="text-muted-foreground px-4 py-3 text-sm">
            Nenhum administrador da plataforma cadastrado.
          </p>
        ) : (
          <ul className="divide-border divide-y">
            {rows.map((admin) => (
              <li key={admin.id} className="flex items-center justify-between gap-3 px-4 py-2">
                {/* `min-w-0` para o `truncate` valer dentro do flex: sem ele o
                    e-mail longo empurra o badge para fora em 390px. */}
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{admin.name}</p>
                  <p className="text-muted-foreground truncate text-xs">{admin.email}</p>
                </div>
                <div className="shrink-0">
                  <UserStatusBadge active={admin.active} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </ScrollRegion>
    </section>
  );
}
