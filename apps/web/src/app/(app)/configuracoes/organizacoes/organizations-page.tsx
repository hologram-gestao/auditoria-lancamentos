'use client';

/**
 * Tela de Organizações — a área da PLATAFORMA (86e36ecwa, onda 2 do épico
 * 86e36ec0q).
 *
 * É a única tela do produto que só `platform_admin` enxerga: a matriz
 * (`manage_platform`) tem ✅ numa coluna só. Admin de organização entra por
 * deep link e recebe `AccessDenied`; o backend devolve 403 nas quatro rotas —
 * é ele a autoridade, isto aqui é para ninguém ficar olhando para o vazio.
 *
 * O que a tela decide: criar organização, renomear e SUSPENDER/reativar. Não
 * há exclusão — organização com clientes e usuários não some (FK RESTRICT no
 * banco); suspender é o caminho, e ele é reversível.
 *
 * Abaixo da tabela mora a única lista de `platform_admin` do produto
 * (`PlatformAdminsSection`), só-leitura: nem o `GET /users` nem o `users_count`
 * das organizações os mostram, então sem ela nem a plataforma sabe quem são os
 * pares dela.
 */

import { Plus, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { OrganizationDialog } from '@/components/features/organizations/organization-dialog';
import { OrganizationStatusConfirm } from '@/components/features/organizations/organization-status-confirm';
import { OrganizationsTable } from '@/components/features/organizations/organizations-table';
import { PlatformAdminsSection } from '@/components/features/organizations/platform-admins-section';
import { AccessDenied } from '@/components/shared/access-denied';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { PaginationBar } from '@/components/ui/pagination-bar';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { useOrganizationsList } from '@/hooks/use-organizations';
import { ApiError } from '@/lib/api/client';
import type { OrganizationItem } from '@/lib/api/organizations';
import { hasPermission, homePathFor } from '@/lib/authz';
import { useAuthStore } from '@/stores/auth';

export default function OrganizationsPage() {
  const currentUser = useAuthStore((s) => s.user);
  const canSee = hasPermission(currentUser, 'manage_platform');

  const [searchInput, setSearchInput] = useState('');
  const debouncedSearch = useDebouncedValue(searchInput, 300);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  // Busca nova recomeça na primeira página (UX padrão das listas do produto).
  // `pageSize` fica FORA daqui de propósito: trocar itens por página dentro de
  // um efeito dispararia o request da página antiga antes do reset — quem muda
  // os dois é o handler, de uma vez só.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch]);

  const queryParams = useMemo(
    () => ({ page, pageSize, search: debouncedSearch || undefined }),
    [page, pageSize, debouncedSearch],
  );
  const { data, isLoading, isError, error } = useOrganizationsList(queryParams, {
    enabled: canSee,
  });

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<OrganizationItem | null>(null);
  // O alvo da confirmação e o "está aberto" são estados SEPARADOS: derivar o
  // `open` de `alvo !== null` obrigaria a limpar o alvo ao fechar, e o diálogo
  // fica montado por 200ms de animação — trocaria o texto para "Reativar" e
  // perderia o nome bem na frente de quem acabou de confirmar.
  const [statusTarget, setStatusTarget] = useState<OrganizationItem | null>(null);
  const [statusOpen, setStatusOpen] = useState(false);

  if (currentUser === null) return null;

  if (!canSee) {
    return (
      <AccessDenied
        message="A administração de organizações é restrita à plataforma."
        backHref={homePathFor(currentUser)}
        backLabel="Voltar para o início"
      />
    );
  }

  const rows = data?.data ?? [];
  const pagination = data?.pagination;
  const errorMessage =
    error instanceof ApiError ? error.userMessage : 'Não foi possível carregar as organizações.';

  return (
    // De `md` para cima: `h-full` + `min-h-0` na área da tabela — ela rola
    // dentro da própria área e a barra de paginação fica no rodapé, sem cobrir
    // a última linha.
    //
    // ABAIXO de `md`, nada disso: altura natural, e quem rola é o `<main>`.
    // Com a seção de administradores embaixo, a disputa por altura em 390px
    // espremia a tabela para UMA linha (medido no print: a organização suspensa
    // saía da área visível, e o `toBeVisible` do Playwright não pega isso —
    // fora da área de rolagem ainda é "visível"). Encher a viewport só vale
    // enquanto a tela couber nela.
    <div className="flex flex-col gap-6 md:h-full">
      <div className="space-y-1">
        <p className="text-muted-foreground text-sm">Configurações &gt; Organizações</p>
        <h1 className="text-2xl font-semibold">Organizações</h1>
        <p className="text-muted-foreground text-sm">
          Cada organização é um escritório com seus próprios clientes, usuários e categorias.
          Suspender bloqueia o acesso das pessoas dela sem apagar nada.
        </p>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative max-w-sm flex-1">
          <Search
            className="text-muted-foreground absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
            aria-hidden="true"
          />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Buscar por nome..."
            className="pl-9"
            aria-label="Buscar organizações"
          />
        </div>
        <Button
          onClick={() => {
            setEditing(null);
            setDialogOpen(true);
          }}
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          Nova organização
        </Button>
      </div>

      <div className="flex flex-col md:min-h-0 md:flex-1">
        <OrganizationsTable
          rows={rows}
          isLoading={isLoading}
          isError={isError}
          errorMessage={errorMessage}
          onEdit={(org) => {
            setEditing(org);
            setDialogOpen(true);
          }}
          onToggleActive={(org) => {
            setStatusTarget(org);
            setStatusOpen(true);
          }}
        />
        <PaginationBar
          page={pagination?.page ?? page}
          pageSize={pagination?.pageSize ?? pageSize}
          total={pagination?.total ?? 0}
          totalPages={pagination?.totalPages ?? 0}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(1);
          }}
          disabled={isLoading}
          itemLabel="organizações"
        />
      </div>

      {/* Abaixo da tabela, e com altura limitada: a área da tabela é `flex-1` e
          uma seção que crescesse com o conteúdo comeria o espaço dela. */}
      <PlatformAdminsSection />

      {/* O alvo NÃO é limpo ao fechar: enquanto o diálogo sai de cena, o
          conteúdo continua sendo o que a pessoa acabou de confirmar. Abrir para
          criar zera explicitamente (`setEditing(null)` no botão). */}
      <OrganizationDialog open={dialogOpen} onOpenChange={setDialogOpen} organization={editing} />
      <OrganizationStatusConfirm
        open={statusOpen}
        onOpenChange={setStatusOpen}
        organization={statusTarget}
      />
    </div>
  );
}
