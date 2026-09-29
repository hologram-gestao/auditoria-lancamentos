'use client';

/**
 * "Gerar arquivo" e o histórico de gerações do arquivo contábil — Sprint 13
 * (FRONT 13.6 / R2 · R3 tela), na prévia do de-para no destino Conta contábil.
 *
 * **Quem vê:** só quem tem `generate_accounting_file` (lib/authz — a MESMA
 * permissão que protege as rotas), e só no destino `conta_contabil`. O
 * `client_manager` e o `client_operator` não veem a seção inteira: a listagem
 * também é negada a eles no servidor.
 *
 * **Gerar:** na prévia gera a ÚLTIMA materialização da competência; em cada
 * versão materializada, gera aquela versão (`materializationId`). Um layout só
 * → usa direto; mais de um → diálogo com o seletor; nenhum → estado que
 * orienta (com `manage_export_layouts`, link para Configurações; sem, "peça ao
 * administrador da organização"). Sem materialização na competência, a ação
 * aparece DESABILITADA com a instrução de materializar a prévia (o 409
 * `ARQUIVO_SEM_MATERIALIZACAO` do servidor segue tratado como estado).
 *
 * **Recusas 409 são ESTADO** (nunca toast): a mensagem do servidor, os
 * CÓDIGOS das categorias a corrigir (as parcelas, na partição; campo e motivo,
 * no texto que não cabe) e o caminho para a lista de decisões — só para quem
 * edita o de-para, com o cliente aberto (ADR-053-FE: o verbo de ação só para
 * quem tem a permissão da ação).
 *
 * **Cliente encerrado:** sem "Gerar" e sem "Baixar"; o histórico continua
 * visível, só leitura, com o motivo. **Baixar** regenera no servidor e confere
 * o SHA-256; o 409 `ARQUIVO_DIVERGENTE` aparece como erro claro na seção.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { AlertTriangle, ArrowRight, Download, FileOutput, Loader2 } from 'lucide-react';
import Link from 'next/link';
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';
import { z } from 'zod';

import { AuthorLabel } from '@/components/features/reconciliations/author-label';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
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
  useAccountingFiles,
  useDownloadAccountingFile,
  useGenerateAccountingFile,
} from '@/hooks/use-accounting-files';
import { useMappingMaterializations } from '@/hooks/use-client-mapping';
import { useExportLayouts } from '@/hooks/use-export-layouts';
import {
  readAccountingFileRefusal,
  REFUSAL_TITLES,
  textReasonLabel,
  type AccountingFileRefusal,
} from '@/lib/accounting-file-refusals';
import { ApiError, NetworkError } from '@/lib/api/client';
import type { AccountingFileGenerationItem, ExportLayoutItem } from '@/lib/contracts';
import { triggerBrowserDownload } from '@/lib/download';
import { layoutFieldLabel } from '@/lib/export-layout-definition';
import { formatBRL, formatCreatedAt, formatReferenceMonth } from '@/lib/format';

/** Rota da tela de layouts (FRONT 13.5) — para onde o estado "sem layout" leva. */
export const EXPORT_LAYOUTS_PATH = '/configuracoes/layouts-exportacao';

/** O que gerar: a ÚLTIMA materialização (`null`) ou uma versão específica. */
export interface GenerationTarget {
  materializationId: string | null;
  version: number | null;
}

interface GeneratorArgs {
  clientId: string;
  destinationType: string;
  competence: string;
  /**
   * A organização DO CLIENTE, só para a plataforma (que enxerga os layouts de
   * todas): o layout tem de ser da organização do cliente, outra é 404. O staff
   * manda `null` e recebe os da própria.
   */
  layoutsOrganizationId: string | null;
  /** `generate_accounting_file` E destino `conta_contabil`. */
  enabled: boolean;
  isClosed: boolean;
}

/**
 * O estado da geração, compartilhado entre a ação da prévia e as ações por
 * versão (que moram na lista de versões materializadas, noutro componente).
 */
export function useAccountingFileGenerator({
  clientId,
  destinationType,
  competence,
  layoutsOrganizationId,
  enabled,
  isClosed,
}: GeneratorArgs) {
  const canAct = enabled && !isClosed;
  const layoutsQuery = useExportLayouts(layoutsOrganizationId, { enabled: canAct });
  const layouts = layoutsQuery.data ?? [];
  const materializationsQuery = useMappingMaterializations(clientId, destinationType, competence, {
    enabled: canAct && competence !== '',
  });
  const hasMaterialization = (materializationsQuery.data ?? []).length > 0;
  const generateMutation = useGenerateAccountingFile(clientId);
  const [pendingTarget, setPendingTarget] = useState<GenerationTarget | null>(null);
  // O alvo do diálogo NÃO é limpo ao fechar (só `dialogOpen` muda): o diálogo
  // fica montado, sai animando e devolve o foco a quem o abriu.
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogTarget, setDialogTarget] = useState<GenerationTarget>({
    materializationId: null,
    version: null,
  });
  // A recusa é da competência em que aconteceu: trocar de competência não pode
  // deixar na tela a recusa de outra.
  const [refusal, setRefusal] = useState<{
    competence: string;
    target: GenerationTarget;
    value: AccountingFileRefusal;
  } | null>(null);

  async function run(target: GenerationTarget, layoutId: string): Promise<boolean> {
    setPendingTarget(target);
    setRefusal(null);
    try {
      const generated = await generateMutation.mutateAsync({
        layoutId,
        competence,
        ...(target.materializationId ? { materializationId: target.materializationId } : {}),
      });
      toast.success(
        `Arquivo gerado: ${generated.lines} ${generated.lines === 1 ? 'linha' : 'linhas'}, ${formatBRL(generated.totalAmount)}.`,
      );
      return true;
    } catch (err) {
      setRefusal({ competence, target, value: readAccountingFileRefusal(err) });
      return false;
    } finally {
      setPendingTarget(null);
    }
  }

  function request(target: GenerationTarget) {
    const only = layouts.length === 1 ? layouts[0] : undefined;
    if (only) {
      void run(target, only.id);
      return;
    }
    if (layouts.length > 1) {
      setDialogTarget(target);
      setDialogOpen(true);
    }
  }

  return {
    /** Pode gerar agora: permissão, cliente aberto e ao menos um layout. */
    available: canAct && layoutsQuery.isSuccess && layouts.length > 0,
    canAct,
    layoutsQuery,
    layouts,
    hasMaterialization,
    materializationsLoading: materializationsQuery.isLoading,
    isPending: generateMutation.isPending,
    pendingTarget,
    request,
    run,
    dialogOpen,
    dialogTarget,
    setDialogOpen,
    refusal: refusal?.competence === competence ? refusal : null,
  };
}

export type AccountingFileGenerator = ReturnType<typeof useAccountingFileGenerator>;

/**
 * O gerador vive num PROVIDER montado só quando a seção existe (permissão +
 * destino `conta_contabil`): fora dele nenhum hook do arquivo roda — nem para
 * quem não pode gerar, nem nos outros destinos —, e a ação por versão, que
 * mora na lista de versões materializadas, lê o mesmo estado sem prop drilling.
 */
const GeneratorContext = createContext<AccountingFileGenerator | null>(null);

export function AccountingFileGeneratorProvider({
  children,
  ...args
}: GeneratorArgs & { children: ReactNode }) {
  const generator = useAccountingFileGenerator(args);
  return <GeneratorContext.Provider value={generator}>{children}</GeneratorContext.Provider>;
}

/**
 * A ação "Gerar arquivo" de UMA versão materializada (lista de versões). Fora
 * do provider — sem permissão ou noutro destino — não renderiza nada.
 */
export function GenerateVersionButton({
  materializationId,
  version,
}: {
  materializationId: string;
  version: number;
}) {
  const generator = useContext(GeneratorContext);
  if (generator === null || !generator.available) return null;
  const pending = generator.pendingTarget?.materializationId === materializationId;
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={() => generator.request({ materializationId, version })}
      disabled={generator.isPending}
      aria-label={`Gerar arquivo da versão ${version}`}
    >
      {pending ? (
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      ) : (
        <FileOutput className="h-4 w-4" aria-hidden="true" />
      )}
      {pending ? 'Gerando…' : 'Gerar arquivo'}
    </Button>
  );
}

interface AccountingFileSectionProps {
  clientId: string;
  competence: string;
  isClosed: boolean;
  /** `manage_export_layouts` — decide o texto do estado "sem layout". */
  canManageLayouts: boolean;
  /** `manage_client_mapping` — quem pode corrigir as categorias recusadas. */
  canEditMapping: boolean;
  /** Leva à lista de decisões filtrada pela categoria (a gaveta abre de lá). */
  onReviewCategory: (categoryCode: string) => void;
}

export function AccountingFileSection({
  clientId,
  competence,
  isClosed,
  canManageLayouts,
  canEditMapping,
  onReviewCategory,
}: AccountingFileSectionProps) {
  const generator = useContext(GeneratorContext);
  if (generator === null) return null;
  const month = formatReferenceMonth(competence);
  const latestPending =
    generator.pendingTarget !== null && generator.pendingTarget.materializationId === null;
  const onlyLayout = generator.layouts.length === 1 ? generator.layouts[0] : undefined;

  return (
    <section
      aria-labelledby="accounting-file-heading"
      className="bg-card space-y-4 rounded-lg border p-4"
      data-testid="accounting-file-section"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h3 id="accounting-file-heading" className="text-sm font-semibold">
            Arquivo contábil de {month}
          </h3>
          <p className="text-muted-foreground text-sm">
            O arquivo que o sistema contábil importa, montado a partir da versão materializada. Nada
            é enviado automaticamente: você gera, confere e baixa.
          </p>
        </div>
        {generator.available && generator.hasMaterialization && (
          <Button
            type="button"
            onClick={() => generator.request({ materializationId: null, version: null })}
            disabled={generator.isPending}
          >
            {latestPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <FileOutput className="h-4 w-4" aria-hidden="true" />
            )}
            {latestPending ? 'Gerando…' : 'Gerar arquivo'}
          </Button>
        )}
      </div>

      <GenerationAvailability
        generator={generator}
        isClosed={isClosed}
        canManageLayouts={canManageLayouts}
        onlyLayout={onlyLayout}
      />

      {generator.refusal && (
        <AccountingFileRefusalState
          refusal={generator.refusal.value}
          version={generator.refusal.target.version}
          canEditMapping={canEditMapping && !isClosed}
          onReviewCategory={onReviewCategory}
        />
      )}

      <AccountingFilesHistory clientId={clientId} competence={competence} isClosed={isClosed} />

      {generator.available && generator.layouts.length > 1 && (
        <GenerateAccountingFileDialog
          open={generator.dialogOpen}
          onOpenChange={generator.setDialogOpen}
          target={generator.dialogTarget}
          layouts={generator.layouts}
          competence={competence}
          onGenerate={generator.run}
          isPending={generator.isPending}
        />
      )}
    </section>
  );
}

/**
 * Por que a ação não está disponível (ou qual layout ela usa). Cada motivo tem
 * o seu texto: encerrado, sem layout (ramificado por permissão), sem
 * materialização, erro ao carregar os layouts.
 */
function GenerationAvailability({
  generator,
  isClosed,
  canManageLayouts,
  onlyLayout,
}: {
  generator: AccountingFileGenerator;
  isClosed: boolean;
  canManageLayouts: boolean;
  onlyLayout: ExportLayoutItem | undefined;
}) {
  if (isClosed) {
    return (
      <p className="text-muted-foreground text-sm" data-testid="accounting-file-closed">
        Cliente encerrado: o arquivo não pode mais ser gerado nem baixado (a chave do cliente foi
        destruída e o histórico dos lançamentos não é mais legível). O registro das gerações abaixo
        continua disponível para consulta.
      </p>
    );
  }
  const { layoutsQuery } = generator;
  if (layoutsQuery.isLoading || generator.materializationsLoading) {
    return <div className="bg-muted h-9 w-40 animate-pulse rounded-md" aria-hidden="true" />;
  }
  if (layoutsQuery.isError) {
    return (
      <div role="alert" className="space-y-2">
        <p className="text-destructive text-sm">
          {layoutsQuery.error instanceof ApiError
            ? layoutsQuery.error.userMessage
            : 'Não foi possível carregar os layouts de exportação.'}
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void layoutsQuery.refetch()}
        >
          Tentar novamente
        </Button>
      </div>
    );
  }
  if (generator.layouts.length === 0) {
    return (
      <div
        role="status"
        className="bg-info-muted text-info ring-info/30 space-y-2 rounded-lg p-4 text-sm ring-1 ring-inset"
        data-testid="accounting-file-no-layout"
      >
        <p className="font-medium">A organização ainda não tem layout de exportação</p>
        {canManageLayouts ? (
          <>
            <p>
              O layout define o formato do arquivo que o sistema contábil importa. Crie o da
              organização a partir do modelo Domínio.
            </p>
            <Button asChild variant="outline" size="sm">
              <Link href={EXPORT_LAYOUTS_PATH}>
                Ir para Layouts de exportação
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Link>
            </Button>
          </>
        ) : (
          <p>
            O layout define o formato do arquivo que o sistema contábil importa. Peça ao
            administrador da organização para criar um em Configurações.
          </p>
        )}
      </div>
    );
  }
  if (!generator.hasMaterialization) {
    return (
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
        <Button type="button" disabled aria-describedby="accounting-file-needs-materialization">
          <FileOutput className="h-4 w-4" aria-hidden="true" />
          Gerar arquivo
        </Button>
        <p id="accounting-file-needs-materialization" className="text-muted-foreground text-sm">
          Materialize a prévia desta competência para gerar o arquivo: ele sai sempre de uma versão
          materializada.
        </p>
      </div>
    );
  }
  return onlyLayout ? (
    <p className="text-muted-foreground text-sm">
      Layout: <span className="text-foreground font-medium">{onlyLayout.name}</span> (versão{' '}
      {onlyLayout.latestVersion}).
    </p>
  ) : (
    <p className="text-muted-foreground text-sm">
      A organização tem {generator.layouts.length} layouts: você escolhe qual ao gerar.
    </p>
  );
}

/** A recusa como ESTADO: título, mensagem do servidor e o que corrigir. */
export function AccountingFileRefusalState({
  refusal,
  version,
  canEditMapping,
  onReviewCategory,
}: {
  refusal: AccountingFileRefusal;
  version: number | null;
  canEditMapping: boolean;
  onReviewCategory: (categoryCode: string) => void;
}) {
  const title =
    refusal.kind === 'refusal' ? REFUSAL_TITLES[refusal.code] : 'Não foi possível gerar o arquivo';
  const codes = refusal.kind === 'refusal' ? refusal.categoryCodes : [];

  return (
    <div
      role="alert"
      data-testid="accounting-file-refusal"
      data-refusal-code={refusal.code}
      className="bg-destructive-muted text-destructive ring-destructive/30 space-y-3 rounded-lg p-4 text-sm ring-1 ring-inset"
    >
      <p className="flex items-start gap-1.5 font-medium">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        {title}
        {version !== null ? ` (versão ${version})` : ''}
      </p>
      <p>{refusal.userMessage}</p>

      {refusal.kind === 'refusal' && refusal.parcels.length > 0 && (
        <dl
          className="grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-[auto_auto] sm:justify-start"
          aria-label="Parcelas da competência"
        >
          {refusal.parcels.map((parcel) => (
            <div key={parcel.key} className="contents">
              <dt>{parcel.label}</dt>
              <dd className="whitespace-nowrap font-medium tabular-nums">
                {formatBRL(parcel.amount)}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {refusal.kind === 'refusal' && refusal.textProblems.length > 0 && (
        <ul className="list-inside list-disc space-y-0.5" aria-label="Textos que não cabem">
          {refusal.textProblems.map((problem, index) => (
            <li key={`${index}:${problem.categoryCode}:${problem.field}:${problem.reason}`}>
              {categoryLabel(problem.categoryCode)}: {layoutFieldLabel(problem.field)}{' '}
              {textReasonLabel(problem.reason)}
            </li>
          ))}
        </ul>
      )}

      {codes.length > 0 && (
        <div className="space-y-2">
          <p className="font-medium">Categorias a corrigir</p>
          <ul className="flex flex-wrap gap-2" aria-label="Categorias a corrigir">
            {codes.map((code) => (
              <li key={code}>
                {canEditMapping && code !== '' ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="text-foreground"
                    onClick={() => onReviewCategory(code)}
                    aria-label={`Corrigir a categoria ${code} na lista de decisões`}
                  >
                    {code}
                    <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                  </Button>
                ) : (
                  <span className="bg-background text-foreground rounded border px-2 py-1 font-medium tabular-nums">
                    {categoryLabel(code)}
                  </span>
                )}
              </li>
            ))}
          </ul>
          <p>
            {canEditMapping
              ? 'Corrija a decisão de cada categoria, materialize a prévia de novo e gere o arquivo.'
              : 'Peça a quem edita o de-para deste cliente para corrigir estas categorias e materializar de novo.'}
          </p>
        </div>
      )}
    </div>
  );
}

function categoryLabel(code: string): string {
  return code === '' ? 'Sem categoria' : code;
}

/** O histórico de gerações da competência — só metadados, com "Baixar". */
function AccountingFilesHistory({
  clientId,
  competence,
  isClosed,
}: {
  clientId: string;
  competence: string;
  isClosed: boolean;
}) {
  const query = useAccountingFiles(clientId, competence);
  const downloadMutation = useDownloadAccountingFile(clientId);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<{ code: string; message: string } | null>(
    null,
  );
  const rows = query.data?.data ?? [];

  // A mensagem do download é da competência em que aconteceu.
  useEffect(() => setDownloadError(null), [competence]);

  async function handleDownload(item: AccountingFileGenerationItem) {
    setDownloadError(null);
    setDownloadingId(item.id);
    try {
      const { blob, filename } = await downloadMutation.mutateAsync(item.id);
      triggerBrowserDownload(blob, filename ?? item.fileName);
    } catch (err) {
      setDownloadError({
        code: err instanceof ApiError ? err.code : 'UNKNOWN',
        message:
          err instanceof ApiError || err instanceof NetworkError
            ? err.userMessage
            : 'Não foi possível baixar o arquivo. Tente novamente.',
      });
    } finally {
      setDownloadingId(null);
    }
  }

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold">Gerações nesta competência</h4>

      {downloadError && (
        <div
          role="alert"
          data-testid="accounting-file-download-error"
          data-error-code={downloadError.code}
          className="bg-destructive-muted text-destructive ring-destructive/30 rounded-lg p-3 text-sm ring-1 ring-inset"
        >
          <p className="font-medium">
            {downloadError.code === 'ARQUIVO_DIVERGENTE'
              ? 'O arquivo não pôde ser reproduzido exatamente como foi gerado'
              : 'O arquivo não foi baixado'}
          </p>
          <p>{downloadError.message}</p>
        </div>
      )}

      {query.isError ? (
        <div
          role="alert"
          className="bg-destructive-muted text-destructive ring-destructive/30 flex flex-col items-start gap-3 rounded-lg p-4 text-sm ring-1 ring-inset sm:flex-row sm:items-center sm:justify-between"
        >
          <span>
            {query.error instanceof ApiError
              ? query.error.userMessage
              : 'Não foi possível carregar as gerações do arquivo.'}
          </span>
          <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
            Tentar novamente
          </Button>
        </div>
      ) : (
        <TableCard aria-busy={query.isFetching}>
          <Table scrollRegionLabel="Gerações do arquivo contábil nesta competência (rolável)">
            <TableHeader>
              <TableRow>
                <TableHead className="whitespace-nowrap">Gerado em</TableHead>
                <TableHead className="whitespace-nowrap">Por</TableHead>
                <TableHead className="whitespace-nowrap">Versão do de-para</TableHead>
                <TableHead className="whitespace-nowrap">Layout</TableHead>
                <TableHead className="whitespace-nowrap text-right">Linhas</TableHead>
                <TableHead className="whitespace-nowrap text-right">Total</TableHead>
                {!isClosed && (
                  <TableHead className="text-right">
                    <span className="sr-only">Ações</span>
                  </TableHead>
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.isLoading
                ? Array.from({ length: 2 }).map((_, index) => (
                    <TableRow key={index} aria-hidden="true">
                      {Array.from({ length: isClosed ? 6 : 7 }).map((__, cell) => (
                        <TableCell key={cell}>
                          <div className="bg-muted h-4 w-full animate-pulse rounded" />
                        </TableCell>
                      ))}
                    </TableRow>
                  ))
                : rows.map((item) => (
                    <TableRow key={item.id}>
                      <TableCell className="whitespace-nowrap">
                        {formatCreatedAt(item.createdAt)}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        <AuthorLabel author={item.author} />
                      </TableCell>
                      <TableCell className="whitespace-nowrap tabular-nums">
                        Versão {item.materializationVersion}
                      </TableCell>
                      <TableCell className="min-w-40">
                        {item.layoutName}{' '}
                        <span className="text-muted-foreground whitespace-nowrap">
                          (v{item.layoutVersion})
                        </span>
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-right tabular-nums">
                        {item.lines}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-right tabular-nums">
                        {formatBRL(item.totalAmount)}
                      </TableCell>
                      {!isClosed && (
                        <TableCell className="text-right">
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => void handleDownload(item)}
                            disabled={downloadingId !== null}
                            aria-label={`Baixar ${item.fileName}`}
                          >
                            {downloadingId === item.id ? (
                              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                            ) : (
                              <Download className="h-4 w-4" aria-hidden="true" />
                            )}
                            Baixar
                          </Button>
                        </TableCell>
                      )}
                    </TableRow>
                  ))}
            </TableBody>
          </Table>
          {!query.isLoading && rows.length === 0 && (
            <TableEmpty className="text-center">
              <p className="text-sm font-medium">Nenhum arquivo gerado nesta competência</p>
              <p className="text-muted-foreground text-sm">
                Cada geração fica registrada aqui, com a versão do de-para, o layout e o total.
              </p>
            </TableEmpty>
          )}
        </TableCard>
      )}
    </div>
  );
}

const generateSchema = z.object({
  layoutId: z.string().min(1, 'Escolha o layout do arquivo.'),
});
type GenerateFormValues = z.infer<typeof generateSchema>;

/** Com mais de um layout: o diálogo de geração com o seletor. */
function GenerateAccountingFileDialog({
  open,
  onOpenChange,
  target,
  layouts,
  competence,
  onGenerate,
  isPending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  target: GenerationTarget;
  layouts: ExportLayoutItem[];
  competence: string;
  onGenerate: (target: GenerationTarget, layoutId: string) => Promise<boolean>;
  isPending: boolean;
}) {
  const form = useForm<GenerateFormValues>({
    resolver: zodResolver(generateSchema),
    defaultValues: { layoutId: '' },
  });

  async function onSubmit(values: GenerateFormValues) {
    // Sucesso OU recusa fecham o diálogo: a recusa é estado NA SEÇÃO, com o que
    // corrigir — não dentro de um diálogo que a pessoa precisa fechar para agir.
    await onGenerate(target, values.layoutId);
    onOpenChange(false);
  }

  const which = target.version === null ? 'da versão mais recente' : `da versão ${target.version}`;

  return (
    <Dialog open={open} onOpenChange={(next) => !isPending && onOpenChange(next)}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Gerar o arquivo contábil</DialogTitle>
          <DialogDescription>
            Arquivo de {formatReferenceMonth(competence)}, a partir {which} do de-para. Escolha o
            layout do sistema contábil.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="layoutId"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Layout</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange} disabled={isPending}>
                    <FormControl>
                      <SelectTrigger aria-label="Layout do arquivo">
                        <SelectValue placeholder="Selecione o layout" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {layouts.map((layout) => (
                        <SelectItem key={layout.id} value={layout.id}>
                          {layout.name} (v{layout.latestVersion})
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter className="gap-2 sm:justify-between sm:gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isPending}
              >
                Cancelar
              </Button>
              <Button type="submit" disabled={isPending}>
                {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                Gerar arquivo
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
