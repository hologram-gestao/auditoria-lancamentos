'use client';

/**
 * Configurações → Destinos do de-para (86e3n70pn, reunião com o Murilo em 08/10).
 *
 * O catálogo do de-para é da ORGANIZAÇÃO: cada destino (demonstrativo contábil,
 * fluxo de caixa, natureza fiscal…) tem a lista de ALVOS para onde as categorias de
 * origem dos clientes vão. Organização nova nasce com os cinco destinos e ZERO
 * alvos, e até aqui não havia tela para cadastrá-los: o de-para mandava "pedir ao
 * administrador da organização", e o administrador era quem estava lendo.
 *
 * O que a tela faz: lista os destinos (nome, tipo, situação, quantos alvos) e, para
 * o destino escolhido (`?destino=<id>` na URL, para o de-para poder mandar direto
 * para ele), a lista paginada dos alvos com criar em LOTE (colar várias linhas
 * `código;nome`), editar o nome e inativar/reativar. Não exclui: alvo referenciado
 * por decisão é 409, e inativar é o caminho que sempre funciona.
 *
 * O `conta_contabil` aparece na lista com a explicação de que o alvo dele é o PLANO
 * CONTÁBIL de cada cliente (S16): o catálogo é recusado nesse destino.
 *
 * Gating: a tela inteira é `manage_mapping_catalog` (plataforma e admin), a mesma
 * permissão que libera o item do menu; o servidor é a autoridade (403 nas escritas).
 * A plataforma lê o catálogo de TODAS as organizações e filtra por uma.
 */

import { Pencil, Plus, Power, PowerOff, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import { isAccountingDestination } from '@/components/features/client-mapping/accounting-destination';
import {
  ALL_ORGANIZATIONS,
  OrganizationFilterSelect,
  useOrganizationOptions,
} from '@/components/features/organizations/organization-select';
import { UserStatusBadge } from '@/components/features/users/user-badges';
import { AccessDenied } from '@/components/shared/access-denied';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { PaginationBar } from '@/components/ui/pagination-bar';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCard,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  useMappingDestinations,
  useMappingTargetsPage,
  useUpdateMappingTarget,
} from '@/hooks/use-client-mapping';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { readEnum, readPositiveInt, useUrlState } from '@/hooks/use-url-state';
import { ApiError } from '@/lib/api/client';
import { hasPermission, homePathFor, isPlatformScoped } from '@/lib/authz';
import type { MappingDestination, MappingTarget } from '@/lib/contracts';
import { cn } from '@/lib/utils';
import { MAX_TARGET_CODE_CHARS } from '@/lib/validation/mapping-catalog';
import { useAuthStore } from '@/stores/auth';

import { AddTargetsDialog } from './add-targets-dialog';
import { EditTargetDialog } from './edit-target-dialog';

/** O parâmetro de URL do destino selecionado — o mesmo que o de-para monta. */
export const DESTINATION_PARAM = 'destino';

const PARAM = {
  destination: DESTINATION_PARAM,
  page: 'page',
  pageSize: 'pageSize',
  status: 'situacao',
  code: 'codigo',
  organization: 'organizacao',
} as const;

const STATUS_VALUES = ['ativos', 'inativos', 'todos'] as const;
type StatusFilter = (typeof STATUS_VALUES)[number];
const STATUS_LABELS: Record<StatusFilter, string> = {
  ativos: 'Só ativos',
  inativos: 'Só inativos',
  todos: 'Ativos e inativos',
};
const DEFAULT_STATUS: StatusFilter = 'todos';
const DEFAULT_PAGE_SIZE = 50;

/** Qual destino abre sem `?destino`: o demonstrativo (o que herda), senão o primeiro. */
export function defaultCatalogDestination(
  destinations: readonly MappingDestination[],
): MappingDestination | null {
  return (
    destinations.find((d) => d.type === 'demonstrativo_contabil') ??
    destinations.find((d) => !isAccountingDestination(d.type)) ??
    destinations[0] ??
    null
  );
}

export function MappingCatalogScreen() {
  const currentUser = useAuthStore((s) => s.user);
  const canSee = hasPermission(currentUser, 'manage_mapping_catalog');
  const platform = isPlatformScoped(currentUser);
  const { get, setMany } = useUrlState();

  const organizationParam = get(PARAM.organization);
  const organizationFilter = platform && organizationParam ? organizationParam : null;
  const destinationsQuery = useMappingDestinations(organizationFilter, {
    enabled: canSee && currentUser !== null,
  });
  const destinations = useMemo(() => destinationsQuery.data ?? [], [destinationsQuery.data]);
  const selected =
    destinations.find((d) => d.id === get(PARAM.destination)) ??
    defaultCatalogDestination(destinations);

  if (currentUser === null) return null;
  if (!canSee) {
    return (
      <AccessDenied
        message="Os destinos do de-para são configurados pelo administrador da organização."
        backHref={homePathFor(currentUser)}
        backLabel="Voltar para o início"
      />
    );
  }

  function selectDestination(id: string) {
    setMany({ [PARAM.destination]: id, [PARAM.page]: null, [PARAM.code]: null });
  }

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <p className="text-muted-foreground text-sm">Configurações &gt; Destinos do de-para</p>
        <h1 className="text-2xl font-semibold">Destinos do de-para</h1>
        <p className="text-muted-foreground max-w-3xl text-sm">
          Um destino é para onde as categorias de origem dos clientes são classificadas (o
          demonstrativo contábil, o fluxo de caixa…). Os alvos são as linhas de cada destino, com
          código e nome. O catálogo vale para todos os clientes da organização.
        </p>
      </div>

      {platform && (
        <OrganizationFilterSelect
          value={organizationFilter ?? ALL_ORGANIZATIONS}
          onValueChange={(value) =>
            setMany({
              [PARAM.organization]: value === ALL_ORGANIZATIONS ? null : value,
              [PARAM.destination]: null,
              [PARAM.page]: null,
            })
          }
          ariaLabel="Filtrar por organização"
          className="w-full sm:w-64"
        />
      )}

      <section aria-labelledby="catalog-destinations-heading" className="space-y-3">
        <h2 id="catalog-destinations-heading" className="text-lg font-semibold">
          Destinos
        </h2>
        <DestinationsTable
          destinations={destinations}
          isLoading={destinationsQuery.isLoading}
          error={destinationsQuery.isError ? destinationsQuery.error : null}
          selectedId={selected?.id ?? null}
          onSelect={selectDestination}
          showOrganization={platform && organizationFilter === null}
        />
      </section>

      {selected !== null &&
        (isAccountingDestination(selected.type) ? (
          <AccountingDestinationExplanation destination={selected} />
        ) : (
          <TargetsSection key={selected.id} destination={selected} get={get} setMany={setMany} />
        ))}
    </div>
  );
}

function DestinationsTable({
  destinations,
  isLoading,
  error,
  selectedId,
  onSelect,
  showOrganization,
}: {
  destinations: MappingDestination[];
  isLoading: boolean;
  error: unknown;
  selectedId: string | null;
  onSelect: (id: string) => void;
  showOrganization: boolean;
}) {
  if (error) {
    return (
      <p role="alert" className="text-destructive text-sm">
        {error instanceof ApiError
          ? error.userMessage
          : 'Não foi possível carregar os destinos do de-para.'}
      </p>
    );
  }
  return (
    <TableCard pageScroll>
      <Table scrollRegionLabel="Destinos da organização (rolável)">
        <TableHeader>
          <TableRow>
            <TableHead>Destino</TableHead>
            {showOrganization && <TableHead>Organização</TableHead>}
            {/* Abaixo de `sm`, situação e alvos descem para baixo do nome: em 390px as
                colunas empurravam "Ver alvos" para fora da tela. */}
            <TableHead className="hidden sm:table-cell">Situação</TableHead>
            <TableHead className="hidden sm:table-cell">Alvos</TableHead>
            <TableHead>
              <span className="sr-only">Ações</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {isLoading
            ? Array.from({ length: 3 }).map((_, index) => (
                <TableRow key={index} aria-hidden="true">
                  {Array.from({ length: showOrganization ? 5 : 4 }).map((__, cell) => (
                    <TableCell key={cell}>
                      <div className="bg-muted h-4 w-full animate-pulse rounded" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            : destinations.map((destination) => {
                const isSelected = destination.id === selectedId;
                const accounting = isAccountingDestination(destination.type);
                return (
                  <TableRow key={destination.id} className={cn(isSelected && 'bg-muted/50')}>
                    <TableCell className="whitespace-normal">
                      <span className="block font-medium">{destination.name}</span>
                      <span className="text-muted-foreground block font-mono text-xs">
                        {destination.type}
                      </span>
                      <span className="mt-1 flex flex-wrap items-center gap-2 sm:hidden">
                        <UserStatusBadge active={destination.active} />
                        <TargetsCount destination={destination} />
                      </span>
                    </TableCell>
                    {showOrganization && (
                      <TableCell className="text-muted-foreground whitespace-nowrap text-sm">
                        <OrganizationNameCell organizationId={destination.organizationId} />
                      </TableCell>
                    )}
                    <TableCell className="hidden sm:table-cell">
                      <UserStatusBadge active={destination.active} />
                    </TableCell>
                    <TableCell className="hidden whitespace-normal sm:table-cell">
                      <TargetsCount destination={destination} />
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        type="button"
                        variant={isSelected ? 'secondary' : 'ghost'}
                        size="sm"
                        aria-pressed={isSelected}
                        aria-label={`Ver alvos de ${destination.name}`}
                        onClick={() => onSelect(destination.id)}
                      >
                        {accounting ? 'Ver detalhes' : 'Ver alvos'}
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
        </TableBody>
      </Table>
      {!isLoading && destinations.length === 0 && (
        <TableEmpty>
          <p className="text-muted-foreground text-sm">Nenhum destino nesta organização.</p>
        </TableEmpty>
      )}
    </TableCard>
  );
}

/** Quantos alvos o destino tem — ou, no `conta_contabil`, de onde eles vêm. */
function TargetsCount({ destination }: { destination: MappingDestination }) {
  if (isAccountingDestination(destination.type)) {
    return <span className="text-muted-foreground text-sm">Plano contábil de cada cliente</span>;
  }
  if (destination.targetsCount === 0) {
    return <span className="text-warning text-sm font-medium">Nenhum alvo</span>;
  }
  return (
    <span className="text-sm tabular-nums">
      {destination.targetsCount} {destination.targetsCount === 1 ? 'alvo' : 'alvos'}
    </span>
  );
}

/**
 * Nome da organização na coluna da plataforma. O destino traz só o id; o nome vem
 * das opções que o filtro de organização já carregou (mesma query, mesmo cache).
 */
function OrganizationNameCell({ organizationId }: { organizationId: string }) {
  const { organizations } = useOrganizationOptions({ enabled: true });
  return <>{organizations.find((o) => o.id === organizationId)?.name ?? '—'}</>;
}

function AccountingDestinationExplanation({ destination }: { destination: MappingDestination }) {
  return (
    <section
      aria-labelledby="catalog-accounting-heading"
      className="bg-info-muted text-info ring-info/30 space-y-2 rounded-lg p-4 text-sm ring-1 ring-inset"
    >
      <h2 id="catalog-accounting-heading" className="font-semibold">
        {destination.name}: sem alvos de catálogo
      </h2>
      <p>
        Neste destino o alvo de cada categoria é uma conta do plano contábil do próprio cliente (o
        plano do sistema contábil onde o escritório lança), importado na tela &quot;Plano
        contábil&quot; de cada cliente. Por isso ele não tem lista de alvos aqui: os códigos de um
        cliente colidiriam com os de outro.
      </p>
    </section>
  );
}

function TargetsSection({
  destination,
  get,
  setMany,
}: {
  destination: MappingDestination;
  get: (key: string) => string | null;
  setMany: (patch: Record<string, string | null>) => void;
}) {
  const page = readPositiveInt(get(PARAM.page), 1);
  const pageSize = readPositiveInt(get(PARAM.pageSize), DEFAULT_PAGE_SIZE);
  const status = readEnum(get(PARAM.status), STATUS_VALUES) ?? DEFAULT_STATUS;
  const codeParam = get(PARAM.code) ?? '';

  const [codeInput, setCodeInput] = useState(codeParam);
  const debouncedCode = useDebouncedValue(codeInput, 300);
  useEffect(() => {
    if (debouncedCode !== codeInput || debouncedCode === codeParam) return;
    setMany({ [PARAM.code]: debouncedCode || null, [PARAM.page]: null });
  }, [debouncedCode, codeInput, codeParam, setMany]);

  const query = useMappingTargetsPage(destination.id, {
    page,
    pageSize,
    active: status === 'todos' ? null : status === 'ativos',
    codePrefix: codeParam || null,
  });
  const toggleMutation = useUpdateMappingTarget(destination.id);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<MappingTarget | null>(null);

  const rows = query.data?.data ?? [];
  const pagination = query.data?.pagination;
  const filtered = status !== DEFAULT_STATUS || codeParam !== '';

  async function toggle(target: MappingTarget) {
    try {
      await toggleMutation.mutateAsync({
        targetId: target.id,
        patch: { active: !target.active },
      });
      toast.success(
        target.active ? `Alvo ${target.code} inativado.` : `Alvo ${target.code} reativado.`,
      );
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível alterar a situação do alvo.',
      );
    }
  }

  return (
    <section aria-labelledby="catalog-targets-heading" className="space-y-3">
      <div className="space-y-1">
        <h2 id="catalog-targets-heading" className="text-lg font-semibold">
          Alvos de {destination.name}
        </h2>
        <p className="text-muted-foreground text-sm">
          {destination.type === 'demonstrativo_contabil'
            ? 'No demonstrativo, o código do alvo é o código da conta de demonstrativo que a origem do cliente declara em cada categoria: é por ele que o "Iniciar de-para" herda. No de-para de um cliente, quem administra o catálogo pode criar estes alvos a partir das contas da origem dele.'
            : 'Inativar um alvo tira ele das opções de decisão sem apagar as decisões que já apontam para ele.'}
        </p>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
        <div className="relative sm:w-64">
          <Label htmlFor="catalog-target-code" className="sr-only">
            Buscar alvo por código
          </Label>
          <Search
            className="text-muted-foreground absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
            aria-hidden="true"
          />
          <Input
            id="catalog-target-code"
            value={codeInput}
            maxLength={MAX_TARGET_CODE_CHARS}
            onChange={(e) => setCodeInput(e.target.value)}
            placeholder="Buscar por código"
            className="pl-9"
          />
        </div>
        <Select
          value={status}
          onValueChange={(value) =>
            setMany({
              [PARAM.status]: value === DEFAULT_STATUS ? null : value,
              [PARAM.page]: null,
            })
          }
        >
          <SelectTrigger className="sm:w-48" aria-label="Situação dos alvos">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_VALUES.map((value) => (
              <SelectItem key={value} value={value}>
                {STATUS_LABELS[value]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button type="button" className="sm:ml-auto" onClick={() => setAdding(true)}>
          <Plus className="h-4 w-4" aria-hidden="true" />
          Adicionar alvos
        </Button>
      </div>

      <div aria-busy={query.isFetching}>
        {query.isError ? (
          <p role="alert" className="text-destructive text-sm">
            {query.error instanceof ApiError
              ? query.error.userMessage
              : 'Não foi possível carregar os alvos.'}
          </p>
        ) : (
          <TableCard pageScroll>
            <Table stickyHeader="page" scrollRegionLabel="Lista de alvos do destino (rolável)">
              <TableHeader>
                <TableRow>
                  <TableHead>Código</TableHead>
                  <TableHead>Nome</TableHead>
                  {/* Abaixo de `sm` a situação desce para baixo do nome: em 390px a
                      coluna empurrava as ações para fora da tela. */}
                  <TableHead className="hidden sm:table-cell">Situação</TableHead>
                  <TableHead>
                    <span className="sr-only">Ações</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {query.isLoading
                  ? Array.from({ length: 4 }).map((_, index) => (
                      <TableRow key={index} aria-hidden="true">
                        {Array.from({ length: 4 }).map((__, cell) => (
                          <TableCell key={cell}>
                            <div className="bg-muted h-4 w-full animate-pulse rounded" />
                          </TableCell>
                        ))}
                      </TableRow>
                    ))
                  : rows.map((target) => (
                      <TableRow key={target.id}>
                        <TableCell className="whitespace-nowrap font-medium tabular-nums">
                          {target.code}
                        </TableCell>
                        <TableCell className="whitespace-normal sm:min-w-48">
                          <span className="block">{target.name}</span>
                          <span className="mt-1 block sm:hidden">
                            <UserStatusBadge active={target.active} />
                          </span>
                        </TableCell>
                        <TableCell className="hidden sm:table-cell">
                          <UserStatusBadge active={target.active} />
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center justify-end gap-1">
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              aria-label={`Editar o alvo ${target.code}`}
                              onClick={() => setEditing(target)}
                            >
                              <Pencil className="h-4 w-4" aria-hidden="true" />
                              {/* Só ícone no celular; o nome acessível é o do `aria-label`. */}
                              <span className="hidden sm:inline">Editar</span>
                            </Button>
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              disabled={toggleMutation.isPending}
                              aria-label={`${target.active ? 'Inativar' : 'Reativar'} o alvo ${target.code}`}
                              onClick={() => void toggle(target)}
                            >
                              {target.active ? (
                                <PowerOff className="h-4 w-4" aria-hidden="true" />
                              ) : (
                                <Power className="h-4 w-4" aria-hidden="true" />
                              )}
                              <span className="hidden sm:inline">
                                {target.active ? 'Inativar' : 'Reativar'}
                              </span>
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
              </TableBody>
            </Table>
            {!query.isLoading && rows.length === 0 && (
              <TableEmpty>
                <div className="flex flex-col items-center gap-3 text-center">
                  <p className="text-muted-foreground text-sm">
                    {filtered
                      ? 'Nenhum alvo neste recorte.'
                      : 'Este destino ainda não tem alvos. Sem eles, a única decisão possível no de-para é "Não mapear".'}
                  </p>
                  {!filtered && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setAdding(true)}
                    >
                      <Plus className="h-4 w-4" aria-hidden="true" />
                      Adicionar alvos
                    </Button>
                  )}
                </div>
              </TableEmpty>
            )}
          </TableCard>
        )}
      </div>

      {!query.isError && (
        <PaginationBar
          page={pagination?.page ?? page}
          pageSize={pagination?.pageSize ?? pageSize}
          total={pagination?.total ?? 0}
          totalPages={pagination?.totalPages ?? 0}
          onPageChange={(next) => setMany({ [PARAM.page]: String(next) })}
          onPageSizeChange={(next) =>
            setMany({ [PARAM.pageSize]: String(next), [PARAM.page]: null })
          }
          disabled={query.isLoading}
          itemLabel="alvos"
        />
      )}

      <AddTargetsDialog open={adding} onOpenChange={setAdding} destination={destination} />
      <EditTargetDialog
        open={editing !== null}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
        destinationId={destination.id}
        target={editing}
      />
    </section>
  );
}
