'use client';

/**
 * Aba "Clientes e fornecedores" do plano contábil (86e3qhhrn).
 *
 * O escritório exporta do Domínio a RELAÇÃO DE CLIENTES e a RELAÇÃO DE FORNECEDORES e
 * importa as duas aqui, uma de cada vez: cada linha vincula uma conta do plano (a coluna
 * `Conta` é o código reduzido) ao CNPJ ou CPF da pessoa, que o servidor guarda cifrado
 * com a chave do cliente. O tipo do vínculo vem do título do relatório — a tela não
 * pergunta nada.
 *
 * **Gating.** A aba só existe para quem tem `manage_client_accounting_chart` (a leitura
 * dos vínculos pede a permissão: documento de terceiro é configuração do escritório); a
 * tela pai decide isso. Aqui só o que depende de ESTADO: cliente encerrado não importa
 * (lê), e cliente sem plano é orientado a importar o plano antes (toda conta do
 * relatório seria "não encontrada").
 *
 * **Por arquivo, tudo ou nada.** Conta que não existe no plano ou é sintética recusa o
 * relatório inteiro (422 ramificado por `code`, nunca toast). O que não recusa mas fica
 * sem vínculo (conta `0`, documento ausente ou inválido no Domínio, conta compartilhada)
 * volta na resposta de sucesso e aparece na tela, com o código da conta e o motivo.
 *
 * **O documento sai MASCARADO do servidor** (a raiz do CNPJ escondida, só filial e
 * verificadores à vista; os quatro últimos do CPF): a tela nunca tem o documento inteiro
 * para mostrar, e não há busca por ele. Nunca escreva a máscara literal num comentário de
 * bloco: `***` seguido de `/` fecha o comentário.
 *
 * **Duas listas, uma por tipo** (86e3tttaf, pedido do Lucas Landim em 20/10/2026):
 * sub-abas "Clientes (N)" e "Fornecedores (N)", com a contagem do `summary` que a lista já
 * devolve, cada uma o `GET …/parties?type=<tipo>` de sempre. À direita da faixa, "Vincular
 * conta" abre a gaveta do vínculo manual; em cada linha, "Remover vínculo" (importado ou
 * manual). As duas ações seguem o gate do "Importar relação": cliente aberto com plano.
 *
 * Estado na URL (`vinculo`, `cliente` por padrão; `vpage`, `vpageSize`), separado do da
 * aba de contas para um filtro não zerar o outro; trocar de sub-aba volta à página 1. A
 * página rola, a tabela não (padrão da tela pai).
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { CheckCircle2, Link2, Loader2, Unlink, Upload, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';

import { FILE_ACCEPT } from '@/components/features/file-origin/file-refusal-notice';
import { FileInputField } from '@/components/shared/file-input-field';
import { Button } from '@/components/ui/button';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import {
  useAccountingPartiesList,
  useImportAccountingParties,
} from '@/hooks/use-client-accounting-chart';
import { readEnum, readPositiveInt, useUrlState } from '@/hooks/use-url-state';
import {
  partyLineReasonLabel,
  readAccountingPartiesRefusal,
} from '@/lib/accounting-parties-errors';
import { ApiError } from '@/lib/api/client';
import {
  ACCOUNTING_CHART_MAX_PAGE_SIZE,
  type ListAccountingPartiesParams,
} from '@/lib/api/client-accounting-chart';
import type {
  AccountingPartiesImportResult,
  AccountingPartyLink,
  AccountingPartyType,
} from '@/lib/contracts';
import {
  accountingPartiesImportSchema,
  type AccountingPartiesImportValues,
} from '@/lib/validation/accounting-parties';

import { AccountingPartiesRefusalNotice } from './accounting-parties-refusal-notice';
import { AccountingPartyLinkSheet } from './accounting-party-link-sheet';
import { AccountingPartyUnlinkDialog } from './accounting-party-unlink-dialog';

const PARAM = { type: 'vinculo', page: 'vpage', pageSize: 'vpageSize' } as const;
const DEFAULT_PAGE_SIZE = 50;
const PARTY_TYPES: readonly AccountingPartyType[] = ['cliente', 'fornecedor'];

export const PARTY_LIST_LABELS: Record<AccountingPartyType, string> = {
  cliente: 'Clientes',
  fornecedor: 'Fornecedores',
};
const REPORT_LABELS: Record<AccountingPartyType, string> = {
  cliente: 'Relação de clientes',
  fornecedor: 'Relação de fornecedores',
};

/** "Relação de clientes importada: 89 pessoas, 89 contas vinculadas (89 novas, …)." */
export function partiesImportSuccessMessage(result: AccountingPartiesImportResult): string {
  const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
  const linked = `${plural(result.contasVinculadas, 'conta vinculada', 'contas vinculadas')}`;
  const detail = `${plural(result.vinculosNovos, 'nova', 'novas')}, ${plural(
    result.vinculosAtualizados,
    'atualizada',
    'atualizadas',
  )}, ${result.vinculosSemMudanca} sem mudança`;
  const skipped =
    result.naoVinculadasTotal > 0
      ? `; ${plural(result.naoVinculadasTotal, 'linha ficou', 'linhas ficaram')} sem vínculo`
      : '';
  return `${REPORT_LABELS[result.relatorio]} importada: ${plural(
    result.linhas,
    'pessoa',
    'pessoas',
  )}, ${linked} (${detail})${skipped}.`;
}

interface AccountingPartiesTabProps {
  clientId: string;
  /** Cliente encerrado: lê, não importa. */
  isClosed: boolean;
  /** Sem plano, não há conta para vincular: a aba orienta a importar o plano antes. */
  hasPlan: boolean;
}

export function AccountingPartiesTab({ clientId, isClosed, hasPlan }: AccountingPartiesTabProps) {
  const { get, setMany } = useUrlState();
  const page = readPositiveInt(get(PARAM.page), 1);
  // Teto do servidor (`le=100`): um link editado à mão não pode virar 422 em loop.
  const pageSize = Math.min(
    readPositiveInt(get(PARAM.pageSize), DEFAULT_PAGE_SIZE),
    ACCOUNTING_CHART_MAX_PAGE_SIZE,
  );
  const type: AccountingPartyType = readEnum(get(PARAM.type), PARTY_TYPES) ?? 'cliente';

  const params = useMemo<ListAccountingPartiesParams>(
    () => ({ page, pageSize, type }),
    [page, pageSize, type],
  );
  const listQuery = useAccountingPartiesList(clientId, params);
  const importMutation = useImportAccountingParties(clientId);
  const [lastImport, setLastImport] = useState<AccountingPartiesImportResult | null>(null);
  const [refusal, setRefusal] = useState<unknown>(null);
  const [linkSheetOpen, setLinkSheetOpen] = useState(false);
  const [linkSheetKey, setLinkSheetKey] = useState(0);
  const [unlinking, setUnlinking] = useState<AccountingPartyLink | null>(null);

  const form = useForm<AccountingPartiesImportValues>({
    resolver: zodResolver(accountingPartiesImportSchema),
    defaultValues: { file: null },
    mode: 'onSubmit',
  });

  async function onSubmit(values: AccountingPartiesImportValues) {
    if (!(values.file instanceof File)) return;
    setRefusal(null);
    try {
      const result = await importMutation.mutateAsync(values.file);
      setLastImport(result);
      toast.success(partiesImportSuccessMessage(result));
      form.reset({ file: null });
    } catch (err) {
      if (readAccountingPartiesRefusal(err) !== null) {
        setRefusal(err);
        return;
      }
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível importar a relação.',
      );
    }
  }

  const rows = listQuery.data?.data ?? [];
  const pagination = listQuery.data?.pagination;
  const summary = listQuery.data?.summary;
  const canImport = !isClosed && hasPlan;

  return (
    <div className="flex flex-col gap-4" data-testid="accounting-parties-tab">
      <p className="text-muted-foreground max-w-3xl text-sm">
        Importe a <strong className="text-foreground font-medium">Relação de clientes</strong> e a{' '}
        <strong className="text-foreground font-medium">Relação de fornecedores</strong> exportadas
        do Domínio (uma de cada vez). Cada linha vincula uma conta do plano contábil ao CNPJ ou CPF
        da pessoa. A conta criada no Domínio depois da última exportação pode ser vinculada à mão,
        em &quot;Vincular conta&quot;. O documento fica cifrado com a chave do cliente e só aparece
        aqui com o início escondido.
      </p>

      {canImport ? (
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            noValidate
            className="bg-card flex flex-col gap-3 rounded-lg border p-4 sm:flex-row sm:items-end"
            aria-label="Importar relação do Domínio"
            data-testid="accounting-parties-import-form"
          >
            <FormField
              control={form.control}
              name="file"
              render={({ field }) => (
                <FormItem className="min-w-0 flex-1">
                  <FormLabel>Relação exportada do Domínio (.xls ou .xlsx)</FormLabel>
                  <FormControl>
                    <FileInputField
                      accept={FILE_ACCEPT}
                      disabled={importMutation.isPending}
                      name={field.name}
                      ref={field.ref}
                      onBlur={field.onBlur}
                      value={field.value}
                      onChange={(file) => {
                        field.onChange(file);
                        setRefusal(null);
                      }}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button type="submit" disabled={importMutation.isPending} className="sm:shrink-0">
              {importMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Upload className="h-4 w-4" aria-hidden="true" />
              )}
              {importMutation.isPending ? 'Importando…' : 'Importar relação'}
            </Button>
          </form>
        </Form>
      ) : (
        <p
          role="note"
          data-testid="accounting-parties-blocked-note"
          className="bg-muted text-foreground rounded-lg p-3 text-sm"
        >
          {isClosed
            ? 'Cliente encerrado: as relações não podem ser importadas. Os vínculos já gravados continuam listados, sem o documento.'
            : 'Importe o plano contábil do cliente antes: cada linha da relação aponta para uma conta do plano, e sem ele nenhuma conta seria encontrada.'}
        </p>
      )}

      {lastImport !== null && (
        <ImportResultPanel result={lastImport} onDismiss={() => setLastImport(null)} />
      )}
      {refusal !== null && <AccountingPartiesRefusalNotice error={refusal} />}

      <dl
        className="grid grid-cols-3 gap-3 text-sm sm:max-w-md"
        aria-label="Resumo dos vínculos"
        data-testid="accounting-parties-summary"
      >
        <Stat label="Contas vinculadas" value={summary?.contasVinculadas} />
        <Stat label="Clientes" value={summary?.clientes} />
        <Stat label="Fornecedores" value={summary?.fornecedores} />
      </dl>

      <Tabs
        value={type}
        onValueChange={(next) => {
          const value = readEnum(next, PARTY_TYPES);
          if (value !== undefined) setMany({ [PARAM.type]: value, [PARAM.page]: null });
        }}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <TabsList className="self-start" aria-label="Tipo do vínculo">
            {PARTY_TYPES.map((value) => (
              <TabsTrigger
                key={value}
                value={value}
                data-testid={`accounting-parties-tab-${value}`}
              >
                {PARTY_LIST_LABELS[value]}
                <span className="ml-1 tabular-nums">
                  ({(value === 'cliente' ? summary?.clientes : summary?.fornecedores) ?? '—'})
                </span>
              </TabsTrigger>
            ))}
          </TabsList>
          {canImport && (
            <Button
              type="button"
              variant="outline"
              className="self-start sm:self-auto"
              onClick={() => {
                setLinkSheetKey((key) => key + 1);
                setLinkSheetOpen(true);
              }}
              data-testid="accounting-parties-link-button"
            >
              <Link2 className="h-4 w-4" aria-hidden="true" />
              Vincular conta
            </Button>
          )}
        </div>

        {PARTY_TYPES.map((value) => (
          <TabsContent key={value} value={value} className="mt-0" aria-busy={listQuery.isFetching}>
            {value === type &&
              (listQuery.isError ? (
                <div
                  role="alert"
                  className="bg-destructive-muted text-destructive space-y-3 rounded-lg p-6"
                >
                  <p className="text-sm font-medium">Não foi possível carregar os vínculos</p>
                  <p className="text-sm">
                    {listQuery.error instanceof ApiError
                      ? listQuery.error.userMessage
                      : 'Tente novamente em instantes.'}
                  </p>
                  <Button variant="outline" size="sm" onClick={() => void listQuery.refetch()}>
                    Tentar novamente
                  </Button>
                </div>
              ) : (
                <TableCard pageScroll>
                  <Table
                    stickyHeader="page"
                    scrollRegionLabel={`Contas vinculadas a ${PARTY_LIST_LABELS[value].toLowerCase()} (rolável)`}
                    className="[&_td]:py-2 [&_th]:h-10"
                  >
                    <TableHeader>
                      <TableRow>
                        <TableHead className="whitespace-nowrap">Código</TableHead>
                        <TableHead>Nome da conta</TableHead>
                        <TableHead className="whitespace-nowrap">Documento</TableHead>
                        {canImport && (
                          <TableHead className="w-16 text-right">
                            <span className="sr-only">Ações</span>
                          </TableHead>
                        )}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {listQuery.isLoading
                        ? Array.from({ length: 4 }).map((_, index) => (
                            <TableRow key={index} aria-hidden="true">
                              {Array.from({ length: canImport ? 4 : 3 }).map((__, cell) => (
                                <TableCell key={cell}>
                                  <div className="bg-muted h-4 w-full animate-pulse rounded" />
                                </TableCell>
                              ))}
                            </TableRow>
                          ))
                        : rows.map((link) => (
                            <PartyRow
                              key={link.accountId}
                              link={link}
                              onUnlink={canImport ? () => setUnlinking(link) : null}
                            />
                          ))}
                    </TableBody>
                  </Table>
                  {!listQuery.isLoading && rows.length === 0 && (
                    <TableEmpty>
                      <p
                        className="text-muted-foreground text-sm"
                        data-testid="accounting-parties-empty"
                      >
                        {value === 'cliente'
                          ? 'Nenhuma conta vinculada a cliente ainda. Importe a Relação de clientes do Domínio ou vincule uma conta à mão.'
                          : 'Nenhuma conta vinculada a fornecedor ainda. Importe a Relação de fornecedores do Domínio ou vincule uma conta à mão.'}
                      </p>
                    </TableEmpty>
                  )}
                </TableCard>
              ))}
          </TabsContent>
        ))}
      </Tabs>

      {!listQuery.isError && (pagination?.total ?? 0) > 0 && (
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

      {canImport && (
        <AccountingPartyLinkSheet
          key={linkSheetKey}
          clientId={clientId}
          open={linkSheetOpen}
          onOpenChange={setLinkSheetOpen}
          defaultPartyType={type}
          onLinked={(linkedType) => {
            if (linkedType !== type) setMany({ [PARAM.type]: linkedType, [PARAM.page]: null });
          }}
        />
      )}
      <AccountingPartyUnlinkDialog
        clientId={clientId}
        link={unlinking}
        onOpenChange={(open) => {
          if (!open) setUnlinking(null);
        }}
      />
    </div>
  );
}

function PartyRow({
  link,
  onUnlink,
}: {
  link: AccountingPartyLink;
  /** `null` = sem a ação (cliente encerrado ou sem plano). */
  onUnlink: (() => void) | null;
}) {
  return (
    <TableRow>
      <TableCell className="whitespace-nowrap font-medium tabular-nums">{link.code}</TableCell>
      <TableCell className="min-w-64 whitespace-normal">
        {link.nameResolved ? (
          link.name
        ) : (
          <span className="text-muted-foreground">Nome indisponível (indecifrável)</span>
        )}
      </TableCell>
      <TableCell className="whitespace-nowrap font-mono text-sm tabular-nums">
        {link.documentResolved && link.documentMasked !== null ? (
          <>
            <span className="sr-only">{link.documentKind === 'cpf' ? 'CPF ' : 'CNPJ '}</span>
            {link.documentMasked}
          </>
        ) : (
          <span className="text-muted-foreground font-sans">Documento indisponível</span>
        )}
      </TableCell>
      {onUnlink !== null && (
        <TableCell className="text-right">
          <TooltipProvider delayDuration={150}>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`Remover vínculo da conta ${link.code}`}
                  onClick={onUnlink}
                  data-testid={`accounting-party-unlink-${link.code}`}
                >
                  <Unlink className="h-4 w-4" aria-hidden="true" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="left" className="text-xs leading-snug">
                Remover vínculo
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </TableCell>
      )}
    </TableRow>
  );
}

function ImportResultPanel({
  result,
  onDismiss,
}: {
  result: AccountingPartiesImportResult;
  onDismiss: () => void;
}) {
  return (
    <div
      role="status"
      data-testid="accounting-parties-import-success"
      className="bg-success-muted text-success ring-success/30 space-y-3 rounded-lg p-4 text-sm ring-1 ring-inset"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="flex items-start gap-2 font-medium">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span>{partiesImportSuccessMessage(result)}</span>
        </p>
        <button
          type="button"
          aria-label="Fechar aviso da importação da relação"
          onClick={onDismiss}
          className="hover:bg-background/60 focus-visible:ring-ring inline-flex h-6 w-6 shrink-0 cursor-pointer items-center justify-center rounded-full focus-visible:outline-none focus-visible:ring-2"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      {result.naoVinculadas.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium">
            Linhas que ficaram sem vínculo (o relatório foi importado mesmo assim):
          </p>
          <TableCard className="bg-background text-foreground max-h-60">
            <Table fill scrollRegionLabel="Linhas sem vínculo (rolável)">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-20 whitespace-nowrap">Linha</TableHead>
                  <TableHead className="w-24 whitespace-nowrap">Conta</TableHead>
                  <TableHead>Motivo</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {result.naoVinculadas.map((item, index) => (
                  <TableRow key={`${item.line}:${index}`}>
                    <TableCell className="tabular-nums">{item.line}</TableCell>
                    <TableCell className="tabular-nums">{item.accountCode ?? '—'}</TableCell>
                    <TableCell className="whitespace-normal">
                      {partyLineReasonLabel(item.reason)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableCard>
          {result.naoVinculadasTotal > result.naoVinculadas.length && (
            <p className="text-xs">
              Mostrando {result.naoVinculadas.length} de {result.naoVinculadasTotal} linhas.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div>
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums">{value ?? '—'}</dd>
    </div>
  );
}
