'use client';

/**
 * Tela "Plano contábil" do cliente — Sprint 16 / R1 · R3 · R5 (FRONT 16.5).
 *
 * O plano do sistema contábil de DESTINO (onde o escritório lança): é dele
 * que o de-para no destino Conta contábil escolhe a conta de cada categoria, e
 * dele sai a conta do BANCO de cada conta de origem. Nome distinto de "Plano de
 * Contas" (S10) de propósito — aquele é o da ORIGEM, sincronizado do Omie; este
 * é IMPORTADO por planilha no modelo da plataforma.
 *
 * **Gating (ADR-042-FE: a tela pergunta pela capacidade).** LER é de todo papel
 * com acesso ao cliente (o backend não declara permissão de leitura).
 * IMPORTAR e ASSOCIAR a conta do banco pedem `manage_client_accounting_chart`
 * (staff: plataforma, admin e gerente da carteira) — as ações SOMEM para o
 * `client_manager` e o `client_operator`, e somem também com cliente
 * encerrado, com o motivo na tela.
 *
 * **Estado na URL** (`page`, `pageSize`, `code`, `type`, `status`): linkável e
 * sobrevive ao F5. O código digitado fica em estado local e só vai à URL depois
 * do debounce. Busca só por CÓDIGO (prefixo): o nome é cifrado com a chave do
 * cliente e não é buscável — dito no campo.
 *
 * **A página rola, a tabela não** (padrão das listas do cliente, 86e3f55bc):
 * `<TableCard pageScroll>` + `<Table stickyHeader="page">`, paginação no fluxo,
 * e a seção da conta do banco depois dela.
 *
 * **A hierarquia aparece** (86e3n70p9): a lista chega do servidor na ordem da
 * classificação (sintética em cima, as analíticas dela abaixo), e a linha mostra
 * isso com o nome recuado pelo grau (`chart-hierarchy.ts`) e a sintética em peso
 * maior. As outras colunas não mudam; nada é ordenado no cliente.
 */

import { CheckCircle2, Search, Upload, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { PaginationBar } from '@/components/ui/pagination-bar';
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
import { useAccountingChartList } from '@/hooks/use-client-accounting-chart';
import { useClientDetail } from '@/hooks/use-clients';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { readEnum, readPositiveInt, useUrlState } from '@/hooks/use-url-state';
import { ApiError } from '@/lib/api/client';
import type { ListAccountingChartParams } from '@/lib/api/client-accounting-chart';
import { hasPermission } from '@/lib/authz';
import type {
  AccountingAccount,
  AccountingAccountStatusFilter,
  AccountingAccountType,
  AccountingChartImportResult,
} from '@/lib/contracts';
import { cn } from '@/lib/utils';
import { useAuthStore } from '@/stores/auth';

import { AccountingChartImportSheet, importSuccessMessage } from './accounting-chart-import-sheet';
import { AccountingChartModel } from './accounting-chart-model';
import { BankAccountsSection } from './bank-accounts-section';
import { classificationDepth, indentClassFor } from './chart-hierarchy';

const PARAM = {
  page: 'page',
  pageSize: 'pageSize',
  code: 'code',
  type: 'type',
  status: 'status',
} as const;

export const DEFAULT_PAGE_SIZE = 50;
/** Teto do termo, igual ao `max_length` do código reduzido no servidor. */
const MAX_CODE_CHARS = 20;
const COLUMN_COUNT = 5;

const TYPE_FILTERS: readonly AccountingAccountType[] = ['analitica', 'sintetica'];
const STATUS_FILTERS: readonly AccountingAccountStatusFilter[] = ['ativa', 'inativa'];

export const ACCOUNT_TYPE_LABELS: Record<AccountingAccountType, string> = {
  analitica: 'Analítica',
  sintetica: 'Sintética',
};
const TYPE_FILTER_LABELS: Record<AccountingAccountType, string> = {
  analitica: 'Analíticas',
  sintetica: 'Sintéticas',
};
const STATUS_FILTER_LABELS: Record<AccountingAccountStatusFilter, string> = {
  ativa: 'Ativas',
  inativa: 'Inativas',
};

const baseBadge =
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

export function AccountingChartScreen({ clientId }: { clientId: string }) {
  const currentUser = useAuthStore((s) => s.user);
  const { get, setMany } = useUrlState();

  const page = readPositiveInt(get(PARAM.page), 1);
  const pageSize = readPositiveInt(get(PARAM.pageSize), DEFAULT_PAGE_SIZE);
  const codeParam = get(PARAM.code) ?? '';
  const type = readEnum(get(PARAM.type), TYPE_FILTERS);
  const status = readEnum(get(PARAM.status), STATUS_FILTERS);

  const [codeInput, setCodeInput] = useState(codeParam);
  const debouncedCode = useDebouncedValue(codeInput, 300);
  // Mesmo guarda da S10: só escreve quando o debounce ASSENTOU e difere da URL.
  useEffect(() => {
    if (debouncedCode !== codeInput || debouncedCode === codeParam) return;
    setMany({ [PARAM.code]: debouncedCode || null, [PARAM.page]: null });
  }, [debouncedCode, codeInput, codeParam, setMany]);

  const queryParams = useMemo<ListAccountingChartParams>(
    () => ({
      page,
      pageSize,
      code: codeParam || null,
      type: type ?? null,
      status: status ?? null,
    }),
    [page, pageSize, codeParam, type, status],
  );

  const listQuery = useAccountingChartList(clientId, queryParams);
  // "O cliente TEM plano?" não sai da lista filtrada: um filtro sem resultado
  // não é "sem plano". Uma sonda de 1 linha, sem filtro, responde pelo total.
  const planProbe = useAccountingChartList(clientId, { page: 1, pageSize: 1 });
  const clientDetail = useClientDetail(clientId);
  const [importOpen, setImportOpen] = useState(false);
  const [importKey, setImportKey] = useState(0);
  const [lastImport, setLastImport] = useState<AccountingChartImportResult | null>(null);

  if (currentUser === null) return null;

  const rows = listQuery.data?.data ?? [];
  const pagination = listQuery.data?.pagination;
  const planTotal = planProbe.data?.pagination.total;
  const hasPlan = (planTotal ?? 0) > 0;
  const noPlan = planTotal === 0;

  const isClosed = clientDetail.data?.closed_at != null;
  const canManage = hasPermission(currentUser, 'manage_client_accounting_chart');
  const showImport = canManage && !isClosed;

  const hasFilters = codeParam !== '' || type !== undefined || status !== undefined;
  // A lista e a sonda são queries independentes: se a lista chega vazia antes
  // da sonda, `noPlan` ainda é falso e o topo e o estado vazio mostrariam o
  // botão ao mesmo tempo. O topo pergunta ao estado vazio, não só à sonda.
  const showsNoPlanState =
    !listQuery.isError && !listQuery.isLoading && rows.length === 0 && !(hasFilters && !noPlan);

  function openImport() {
    setImportKey((k) => k + 1);
    setImportOpen(true);
  }

  function clearFilters() {
    setCodeInput('');
    setMany({
      [PARAM.code]: null,
      [PARAM.type]: null,
      [PARAM.status]: null,
      [PARAM.page]: null,
    });
  }

  const importButton = (
    <Button type="button" onClick={openImport} disabled={planProbe.isLoading}>
      <Upload className="h-4 w-4" aria-hidden="true" />
      {hasPlan ? 'Reimportar planilha' : 'Importar planilha'}
    </Button>
  );

  return (
    // `data-page-scroll`: esta tela é do padrão em que a PÁGINA rola (§7
    // Frontend). É o que faz o `ClientShell` soltar a altura fixa do padrão
    // FILL; sem ele a página termina colada na borda da janela (86e3gkd80).
    <section
      aria-labelledby="accounting-chart-heading"
      data-page-scroll
      className="flex flex-col gap-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 id="accounting-chart-heading" className="text-xl font-semibold">
            Plano contábil
          </h1>
          <p className="text-muted-foreground max-w-3xl text-sm">
            O plano de contas do sistema contábil onde o escritório lança — diferente das Categorias
            do Omie, que são a classificação da origem. É dele que o de-para escolhe a conta de cada
            categoria e a conta do banco de cada conta de origem.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {/* Sem plano, o botão mora no estado vazio, junto do modelo — um só na tela. */}
          {showImport && !noPlan && !showsNoPlanState && importButton}
          {canManage && isClosed && (
            <p className="text-muted-foreground max-w-xs text-sm">
              Cliente encerrado: a importação está indisponível. O plano já importado continua
              disponível para leitura.
            </p>
          )}
        </div>
      </div>

      {lastImport !== null && (
        <div
          role="status"
          data-testid="accounting-chart-import-success"
          className="bg-success-muted text-success ring-success/30 space-y-3 rounded-lg p-4 text-sm ring-1 ring-inset"
        >
          <div className="flex items-start justify-between gap-2">
            <p className="flex items-start gap-2 font-medium">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <span>{importSuccessMessage(lastImport)}</span>
            </p>
            <button
              type="button"
              aria-label="Fechar aviso da importação"
              onClick={() => setLastImport(null)}
              className="hover:bg-background/60 focus-visible:ring-ring inline-flex h-6 w-6 shrink-0 cursor-pointer items-center justify-center rounded-full focus-visible:outline-none focus-visible:ring-2"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
          <dl className="grid grid-cols-3 gap-2">
            <Stat label="Contas na planilha" value={lastImport.contas} />
            <Stat label="Novas" value={lastImport.contasNovas} />
            <Stat label="Inativadas" value={lastImport.contasInativadas} />
          </dl>
        </div>
      )}

      {/* Barra de filtros: só aparece com plano — sem plano, não há o que filtrar. */}
      {!noPlan && (
        <div className="flex flex-col gap-3 xl:flex-row xl:flex-wrap xl:items-center">
          <div className="xl:w-72">
            <Label htmlFor="accounting-code-search" className="sr-only">
              Buscar por código
            </Label>
            <div className="relative">
              <Search
                className="text-muted-foreground absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
                aria-hidden="true"
              />
              <Input
                id="accounting-code-search"
                value={codeInput}
                maxLength={MAX_CODE_CHARS}
                onChange={(e) => setCodeInput(e.target.value)}
                placeholder="Buscar por código"
                aria-describedby="accounting-code-search-hint"
                className="pl-9 pr-28"
              />
              {/* O nome é cifrado: não existe busca por nome, e a tela diz isso. */}
              <span
                id="accounting-code-search-hint"
                className="text-muted-foreground pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs"
              >
                início do código
              </span>
            </div>
          </div>

          <FilterGroup
            label="Tipo"
            value={type ?? null}
            options={TYPE_FILTERS.map((value) => ({ value, label: TYPE_FILTER_LABELS[value] }))}
            onChange={(value) => setMany({ [PARAM.type]: value, [PARAM.page]: null })}
          />
          <FilterGroup
            label="Situação"
            value={status ?? null}
            options={STATUS_FILTERS.map((value) => ({
              value,
              label: STATUS_FILTER_LABELS[value],
            }))}
            onChange={(value) => setMany({ [PARAM.status]: value, [PARAM.page]: null })}
          />

          {hasFilters && (
            <Button type="button" variant="outline" size="sm" onClick={clearFilters}>
              Limpar filtros
            </Button>
          )}
        </div>
      )}

      <div aria-busy={listQuery.isFetching}>
        {listQuery.isError ? (
          <ErrorState
            message={
              listQuery.error instanceof ApiError
                ? listQuery.error.userMessage
                : 'Não foi possível carregar o plano contábil.'
            }
            onRetry={() => void listQuery.refetch()}
          />
        ) : (
          <TableCard pageScroll>
            <Table
              stickyHeader="page"
              scrollRegionLabel="Contas do plano contábil (rolável)"
              className="[&_td]:py-2 [&_th]:h-10"
            >
              <TableHeader>
                <TableRow>
                  <TableHead className="whitespace-nowrap">Código</TableHead>
                  <TableHead className="whitespace-nowrap">Classificação</TableHead>
                  <TableHead>Nome</TableHead>
                  <TableHead>Tipo</TableHead>
                  <TableHead>Situação</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {listQuery.isLoading ? (
                  <TableSkeletonRows />
                ) : (
                  rows.map((account) => <AccountRow key={account.id} account={account} />)
                )}
              </TableBody>
            </Table>
            {!listQuery.isLoading && rows.length === 0 && (
              <TableEmpty>
                {hasFilters && !noPlan ? (
                  <div className="flex flex-col items-center gap-3 text-center">
                    <p className="text-muted-foreground text-sm">
                      Nenhuma conta encontrada para este recorte.
                    </p>
                    <Button type="button" variant="outline" size="sm" onClick={clearFilters}>
                      Limpar filtros
                    </Button>
                  </div>
                ) : (
                  <NoPlanState
                    action={showImport ? importButton : null}
                    canManage={canManage}
                    isClosed={isClosed}
                  />
                )}
              </TableEmpty>
            )}
          </TableCard>
        )}
      </div>

      {!listQuery.isError && !noPlan && (
        <PaginationBar
          page={pagination?.page ?? page}
          pageSize={pagination?.pageSize ?? pageSize}
          total={pagination?.total ?? 0}
          totalPages={pagination?.totalPages ?? 0}
          onPageChange={(next) => setMany({ [PARAM.page]: String(next) })}
          onPageSizeChange={(next) =>
            setMany({ [PARAM.pageSize]: String(next), [PARAM.page]: null })
          }
          disabled={listQuery.isLoading}
          itemLabel="contas"
        />
      )}

      <BankAccountsSection
        clientId={clientId}
        canManage={canManage}
        isClosed={isClosed}
        hasPlan={hasPlan}
      />

      {showImport && (
        <AccountingChartImportSheet
          key={importKey}
          clientId={clientId}
          open={importOpen}
          onOpenChange={setImportOpen}
          hasPlan={hasPlan}
          onImported={(result) => {
            setLastImport(result);
            setCodeInput('');
            setMany({
              [PARAM.code]: null,
              [PARAM.type]: null,
              [PARAM.status]: null,
              [PARAM.page]: null,
            });
          }}
        />
      )}
    </section>
  );
}

function FilterGroup<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T | null;
  options: { value: T; label: string }[];
  onChange: (value: T | null) => void;
}) {
  const all: { value: T | null; label: string }[] = [{ value: null, label: 'Todas' }, ...options];
  return (
    <div
      role="group"
      aria-label={label}
      className="bg-background inline-flex flex-wrap self-start rounded-md border p-0.5 xl:self-auto"
    >
      {all.map((option) => {
        const pressed = value === option.value;
        return (
          <Button
            key={option.label}
            type="button"
            variant="ghost"
            size="sm"
            aria-pressed={pressed}
            className={cn('h-8', pressed && 'bg-accent text-accent-foreground')}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </Button>
        );
      })}
    </div>
  );
}

function AccountRow({ account }: { account: AccountingAccount }) {
  const typeLabel = ACCOUNT_TYPE_LABELS[account.type] ?? account.type;
  const depth = classificationDepth(account.classification);
  const isSynthetic = account.type === 'sintetica';
  return (
    <TableRow>
      <TableCell className="whitespace-nowrap font-medium tabular-nums">{account.code}</TableCell>
      <TableCell className="text-muted-foreground whitespace-nowrap tabular-nums">
        {account.classification ?? '—'}
      </TableCell>
      {/* `min-w-72`: o recuo mais fundo (5rem) sai da largura do nome, não da
          linha — em 390px a tabela rola na horizontal e o nome continua legível
          sem virar quatro linhas. */}
      <TableCell className="min-w-72 whitespace-normal">
        {/* O recuo pelo grau e o peso da sintética são a hierarquia que a ordem
            do servidor já traz; o `data-depth` é o que o teste e o e2e leem. */}
        <span
          data-depth={depth}
          className={cn('block', indentClassFor(depth), isSynthetic && 'font-semibold')}
        >
          {/* Nome que a chave do cliente não abre (cliente encerrado): texto
              NEUTRO, sem cara de nome de conta. */}
          {account.nameResolved ? (
            account.name
          ) : (
            <span className="text-muted-foreground font-normal">
              Nome indisponível (indecifrável)
            </span>
          )}
        </span>
      </TableCell>
      <TableCell>
        <span
          className={cn(
            baseBadge,
            account.type === 'analitica'
              ? 'bg-info-muted text-info ring-info/30'
              : 'bg-muted text-muted-foreground ring-border',
          )}
        >
          <span className="sr-only">Tipo: </span>
          {typeLabel}
        </span>
      </TableCell>
      <TableCell>
        <span
          className={cn(
            baseBadge,
            account.active
              ? 'bg-success-muted text-success ring-success/30'
              : 'bg-muted text-muted-foreground ring-border',
          )}
        >
          <span className="sr-only">Situação: </span>
          {account.active ? 'Ativa' : 'Inativa'}
        </span>
      </TableCell>
    </TableRow>
  );
}

function TableSkeletonRows() {
  return (
    <>
      {Array.from({ length: 5 }).map((_, index) => (
        <TableRow key={index} aria-hidden="true">
          {Array.from({ length: COLUMN_COUNT }).map((__, cell) => (
            <TableCell key={cell}>
              <div className="bg-muted h-4 w-full animate-pulse rounded" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}

/**
 * Cliente SEM plano. Explica o modelo (é o que a pessoa precisa para montar a
 * planilha) e oferece Importar a quem pode; os outros sabem a quem pedir.
 */
function NoPlanState({
  action,
  canManage,
  isClosed,
}: {
  action: React.ReactNode;
  canManage: boolean;
  isClosed: boolean;
}) {
  return (
    <div
      className="mx-auto flex max-w-xl flex-col gap-4 text-left"
      data-testid="accounting-chart-empty"
    >
      <div className="space-y-1 text-center">
        <p className="text-sm font-medium">Este cliente ainda não tem plano contábil</p>
        <p className="text-muted-foreground text-sm">
          {isClosed
            ? 'O cliente foi encerrado antes de importar o plano, e a importação não fica disponível para clientes encerrados.'
            : canManage
              ? 'Exporte o plano do sistema contábil numa planilha neste modelo e importe aqui.'
              : 'Quando alguém do escritório importar o plano, as contas aparecem aqui.'}
        </p>
      </div>
      <AccountingChartModel headingId="accounting-chart-empty-model" />
      {action !== null && <div className="flex justify-center">{action}</div>}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-xs">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums">{value}</dd>
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="bg-destructive-muted text-destructive space-y-3 rounded-lg p-6">
      <p className="text-sm font-medium">Não foi possível carregar o plano contábil</p>
      <p className="text-sm">{message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Tentar novamente
      </Button>
    </div>
  );
}
