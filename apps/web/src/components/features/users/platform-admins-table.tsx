'use client';

/**
 * Quem administra a PLATAFORMA — a aba só-leitura da tela de Usuários
 * (86e3chrxw; antes era uma seção no rodapé de Organizações, 86e36ed4b).
 *
 * Por que existe: ninguém mais enxerga os `platform_admin`. O `GET /users`
 * filtra `scope='system'` no próprio SELECT (anti-IDOR da 86e36ecar: o admin
 * de uma organização não pode alcançar a conta da plataforma) e o
 * `users_count` de cada organização conta só o staff dela — usuário de
 * plataforma tem `organization_id` nulo e fica fora de todo total. Sem esta
 * lista, nem a plataforma sabe quem são os pares dela.
 *
 * Por que uma aba PRÓPRIA e não um filtro na tabela de staff: promover e
 * despromover é só pelo script (`promote_platform_admin.py`, decisão Q3), e
 * `PATCH /users/{id}` responde 404 para uma linha de plataforma. Dentro da
 * tabela de staff, as ações da linha e o botão de criar teriam de ser
 * escondidos caso a caso — ação na tela que o servidor nega é defeito (§4.9).
 * Numa tabela sem coluna de ações, isso é verdade por construção.
 *
 * Só a plataforma monta este componente (a aba nem existe para o admin de
 * organização): a rota é `ManagePlatformDep`, e só ela pode saber quem é
 * plataforma.
 */

import { format } from 'date-fns';
import { ptBR } from 'date-fns/locale';

import { UserStatusBadge } from '@/components/features/users/user-badges';
import {
  Table,
  TableBody,
  TableCard,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { usePlatformAdminsList } from '@/hooks/use-organizations';

// O nome da região rolável da tabela é DIFERENTE do rótulo da aba de
// propósito: `getByRole` do Playwright casa por substring, e "Administradores
// da plataforma" casaria com a aba (`tab`) e com a região ao mesmo tempo.
// Mesmo desencontro deliberado de `client-managers-section`.
export const NOME_DA_REGIAO = 'Lista de administradores da plataforma';

const COL_COUNT = 4;

export function PlatformAdminsTable() {
  const { data, isLoading, isError } = usePlatformAdminsList();
  const rows = data ?? [];

  return (
    <div className="space-y-3">
      <p className="text-muted-foreground text-sm">
        Alcançam todas as organizações e não pertencem a nenhuma. Entram e saem apenas pelo script
        de promoção, nunca por esta tela.
      </p>

      {/* `TableCard` sem `fill`: a tela de Usuários tem altura natural (quem
          rola é o `<main>`), e o `fill` só faz sentido dentro de uma área com
          altura limitada. A lista não é paginada no backend — são as poucas
          contas da plataforma, nunca centenas. */}
      <TableCard>
        <Table scrollRegionLabel={NOME_DA_REGIAO}>
          <TableHeader>
            <TableRow>
              <TableHead>Nome</TableHead>
              <TableHead>E-mail</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Cadastrado em</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell
                  colSpan={COL_COUNT}
                  className="text-muted-foreground py-10 text-center text-sm"
                >
                  Carregando...
                </TableCell>
              </TableRow>
            ) : isError ? (
              <TableRow>
                <TableCell
                  colSpan={COL_COUNT}
                  className="text-destructive py-10 text-center text-sm"
                >
                  Não foi possível carregar os administradores da plataforma.
                </TableCell>
              </TableRow>
            ) : rows.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={COL_COUNT}
                  className="text-muted-foreground py-10 text-center text-sm"
                >
                  Nenhum administrador da plataforma cadastrado.
                </TableCell>
              </TableRow>
            ) : (
              rows.map((admin) => (
                // Desativado continua na lista, marcado pelo selo: desativar não
                // tira o escopo, e a conta volta a alcançar tudo se for reativada.
                // SEM `opacity-60` na linha: a opacidade mistura o texto com o
                // fundo e derruba o contraste do e-mail, da data e do próprio selo
                // abaixo de 4,5:1 (medido pelo gate: 3,89 e 2,39 no tema
                // Hologram). O rótulo é o distintivo, não a cor.
                <TableRow key={admin.id}>
                  <TableCell className="font-medium">{admin.name}</TableCell>
                  <TableCell className="text-muted-foreground">{admin.email}</TableCell>
                  <TableCell>
                    <UserStatusBadge active={admin.active} />
                  </TableCell>
                  <TableCell className="text-muted-foreground whitespace-nowrap text-sm">
                    {format(new Date(admin.created_at), "dd 'de' MMM 'de' yyyy", { locale: ptBR })}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </TableCard>
    </div>
  );
}
