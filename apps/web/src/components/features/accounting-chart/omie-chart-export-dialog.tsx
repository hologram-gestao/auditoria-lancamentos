'use client';

/**
 * "Gerar planilha da Omie" (86e3qhhrr).
 *
 * A Omie não recebe plano de contas pela API: a entrada é a planilha
 * `Omie_Plano_Contas` do Painel do Contador. O servidor preenche o modelo da Omie
 * com o plano contábil do cliente e, nas contas vinculadas a um cliente ou
 * fornecedor, o CNPJ/CPF que a Omie usa para ligar a conta ao cadastro.
 *
 * O diálogo mostra a PRÉVIA antes de baixar (contagens, o que impede a geração e
 * quantos documentos não têm cadastro no Omie do cliente: na importação a Omie não
 * cria o cadastro e a conta cai como "diversos"). A recusa 409 do download (o plano
 * pode ter mudado depois da prévia) aparece AQUI, nomeando as contas pelo código.
 *
 * Quem chama esconde o botão de quem não tem `generate_accounting_file` e de cliente
 * encerrado (o servidor nega os dois de qualquer jeito).
 *
 * Depois do download o diálogo fica aberto com o PRÓXIMO passo (86e3qhht4): importado o
 * plano na Omie, as categorias se configuram com o guia do de-para, no destino conta
 * contábil. Só texto e link, e só para cliente com Omie: é a MESMA pergunta que o
 * de-para faz para mostrar o botão do guia (`hasOmieConnection`), senão o link
 * mandaria procurar uma ação que o destino não oferece.
 */

import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Download,
  FileSpreadsheet,
  Info,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';
import { toast } from 'sonner';

import { accountingMappingPath } from '@/components/features/client-mapping/accounting-destination';
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
  useExportOmieChart,
  useOmieChartExportPreview,
  useRefreshOmieChartExportPreview,
} from '@/hooks/use-client-accounting-chart';
import { useClientDetail } from '@/hooks/use-clients';
import {
  omieExportReasonLabel,
  readOmieExportRefusal,
  type OmieExportRefusalProblem,
} from '@/lib/accounting-chart-errors';
import { ApiError, NetworkError } from '@/lib/api/client';
import type { OmieExportPreview, OmieRegistryCheck } from '@/lib/contracts';
import { triggerBrowserDownload } from '@/lib/download';
import { hasOmieConnection } from '@/lib/origin-capabilities';

interface OmieChartExportActionProps {
  clientId: string;
}

export function OmieChartExportAction({ clientId }: OmieChartExportActionProps) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button type="button" variant="outline" onClick={() => setOpen(true)}>
        <FileSpreadsheet className="h-4 w-4" aria-hidden="true" />
        Gerar planilha da Omie
      </Button>
      {open && <OmieChartExportDialog open={open} onOpenChange={setOpen} clientId={clientId} />}
    </>
  );
}

function OmieChartExportDialog({
  open,
  onOpenChange,
  clientId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
}) {
  const preview = useOmieChartExportPreview(clientId, { enabled: open });
  const exportMutation = useExportOmieChart(clientId);
  const refreshMutation = useRefreshOmieChartExportPreview(clientId);
  const [refusal, setRefusal] = useState<OmieExportRefusalProblem[] | null>(null);
  const [downloaded, setDownloaded] = useState(false);
  const data = preview.data;
  const problems = refusal ?? data?.problems ?? [];
  const canDownload = !downloaded && data !== undefined && problems.length === 0;

  async function handleDownload() {
    try {
      const { blob, filename } = await exportMutation.mutateAsync();
      triggerBrowserDownload(blob, filename ?? 'Omie_Plano_Contas.xlsx');
      toast.success('Planilha da Omie baixada. Importe no Painel do Contador da Omie.');
      setDownloaded(true);
    } catch (err) {
      const problemsFromServer = readOmieExportRefusal(err);
      if (problemsFromServer !== null) {
        setRefusal(problemsFromServer);
        return;
      }
      toast.error(
        err instanceof ApiError || err instanceof NetworkError
          ? err.userMessage
          : 'Não foi possível gerar a planilha da Omie.',
      );
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Gerar planilha da Omie</DialogTitle>
          <DialogDescription>
            A planilha de importação de plano de contas do Painel do Contador da Omie, preenchida
            com o plano contábil deste cliente. Nas contas ligadas a um cliente ou fornecedor, ela
            leva o CNPJ ou CPF, que é como a Omie liga a conta ao cadastro.
          </DialogDescription>
        </DialogHeader>

        {downloaded ? (
          <NextStepNotice clientId={clientId} />
        ) : preview.isLoading ? (
          <p className="text-muted-foreground flex items-center gap-2 text-sm" role="status">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Conferindo o plano contábil…
          </p>
        ) : preview.isError ? (
          <div role="alert" className="space-y-2 text-sm">
            <p className="text-destructive">
              {preview.error instanceof ApiError || preview.error instanceof NetworkError
                ? preview.error.userMessage
                : 'Não foi possível conferir o plano contábil.'}
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void preview.refetch()}
            >
              Tentar de novo
            </Button>
          </div>
        ) : data ? (
          <div className="space-y-4">
            <PreviewCounts data={data} />
            {problems.length > 0 ? (
              <ProblemsNotice problems={problems} />
            ) : (
              <RegistryNotice
                check={data.omieRegistry}
                refreshing={refreshMutation.isPending}
                onRefresh={() =>
                  refreshMutation.mutate(undefined, {
                    onError: (err) =>
                      toast.error(
                        err instanceof ApiError || err instanceof NetworkError
                          ? err.userMessage
                          : 'Não foi possível conferir o cadastro do Omie de novo.',
                      ),
                  })
                }
              />
            )}
          </div>
        ) : null}

        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={exportMutation.isPending}
          >
            {canDownload ? 'Cancelar' : 'Fechar'}
          </Button>
          {canDownload && (
            <Button
              type="button"
              onClick={() => void handleDownload()}
              disabled={exportMutation.isPending}
            >
              {exportMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Download className="h-4 w-4" aria-hidden="true" />
              )}
              {exportMutation.isPending ? 'Gerando…' : 'Baixar planilha'}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function NextStepNotice({ clientId }: { clientId: string }) {
  // O shell já carregou este detalhe: vem do cache, sem segundo request.
  const client = useClientDetail(clientId).data;
  const offersGuide = hasOmieConnection(client?.connections ?? []);
  return (
    <div role="status" className="space-y-3 text-sm" data-testid="omie-export-next-step">
      <p className="text-success flex items-start gap-2">
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span>Planilha baixada. Importe no Painel do Contador da Omie.</span>
      </p>
      {offersGuide && (
        <>
          <p className="text-muted-foreground">
            Depois de importar o plano na Omie, configure as categorias com o guia do de-para.
          </p>
          <Link
            href={accountingMappingPath(clientId)}
            className="text-link inline-flex items-center gap-1 font-medium underline underline-offset-4"
          >
            Abrir o de-para na conta contábil
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </Link>
        </>
      )}
    </div>
  );
}

function PreviewCounts({ data }: { data: OmieExportPreview }) {
  const extras: string[] = [];
  if (data.inativas > 0) {
    extras.push(
      `${data.inativas} ${data.inativas === 1 ? 'conta inativa sai' : 'contas inativas saem'} também`,
    );
  }
  if (data.ausentes > 0) {
    extras.push(
      `${data.ausentes} ${data.ausentes === 1 ? 'conta não veio' : 'contas não vieram'} na última importação do plano e ${data.ausentes === 1 ? 'sai' : 'saem'} também`,
    );
  }
  return (
    <div className="space-y-2">
      <dl className="grid grid-cols-3 gap-2" data-testid="omie-export-counts">
        <Count label="Contas" value={data.contas} />
        <Count label="Com CNPJ/CPF" value={data.vinculadas} />
        <Count label="Sem vínculo" value={data.semVinculo} />
      </dl>
      {extras.length > 0 && <p className="text-muted-foreground text-sm">{extras.join('; ')}.</p>}
    </div>
  );
}

function Count({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-muted rounded-md px-3 py-2">
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums">{value.toLocaleString('pt-BR')}</dd>
    </div>
  );
}

const UNCHECKED_MESSAGES: Record<string, string> = {
  sem_omie:
    'Este cliente não está conectado ao Omie, então não foi possível conferir se os CNPJ/CPF já têm cadastro lá.',
  origem_com_erro:
    'A conexão deste cliente com o Omie está com erro (reconecte em Contas Bancárias), então não foi possível conferir se os CNPJ/CPF já têm cadastro lá.',
  omie_indisponivel:
    'O Omie não respondeu agora, então não foi possível conferir se os CNPJ/CPF já têm cadastro lá.',
};

function RegistryNotice({
  check,
  refreshing,
  onRefresh,
}: {
  check: OmieRegistryCheck;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  if (!check.checked) {
    return (
      <p
        role="status"
        className="bg-muted text-muted-foreground flex items-start gap-2 rounded-md p-3 text-sm"
      >
        <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span>
          {UNCHECKED_MESSAGES[check.reason ?? 'omie_indisponivel'] ??
            UNCHECKED_MESSAGES.omie_indisponivel}{' '}
          Na importação, a Omie não cria cadastro: a conta cujo documento não existe lá fica como
          diversos.
        </span>
      </p>
    );
  }
  const missing = check.missing ?? 0;
  if (missing === 0) {
    return (
      <p role="status" className="text-success flex items-start gap-2 text-sm">
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span>A planilha está pronta para importar no Painel do Contador da Omie.</span>
      </p>
    );
  }
  return (
    <div
      role="status"
      data-testid="omie-export-registry-warning"
      className="bg-warning-muted text-warning ring-warning/30 space-y-2 rounded-md p-3 text-sm ring-1 ring-inset"
    >
      <p className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span>
          {missing === 1
            ? '1 conta tem um CNPJ/CPF que não está no cadastro do Omie deste cliente.'
            : `${missing} contas têm um CNPJ/CPF que não está no cadastro do Omie deste cliente.`}{' '}
          Na importação, a Omie não cria o cadastro e essas contas ficam como diversos. Cadastre o
          cliente ou fornecedor no Omie antes, se quiser a ligação, e confira de novo.
        </span>
      </p>
      <Button type="button" variant="outline" size="sm" onClick={onRefresh} disabled={refreshing}>
        {refreshing ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        ) : (
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
        )}
        {refreshing ? 'Conferindo…' : 'Conferir de novo no Omie'}
      </Button>
    </div>
  );
}

function ProblemsNotice({ problems }: { problems: OmieExportRefusalProblem[] }) {
  return (
    <div
      role="alert"
      data-testid="omie-export-problems"
      className="bg-destructive-muted text-destructive ring-destructive/30 space-y-2 rounded-md p-3 text-sm ring-1 ring-inset"
    >
      <p className="font-medium">A planilha não pode ser gerada. Corrija no plano contábil:</p>
      <ul className="space-y-1.5">
        {problems.map((problem) => (
          <li key={problem.reason}>
            <span>{omieExportReasonLabel(problem.reason)}</span>
            {problem.accountCodes.length > 0 && (
              <span className="block break-words font-mono text-xs">
                {problem.count === 1 ? 'Conta ' : 'Contas '}
                {problem.accountCodes.join(', ')}
                {problem.count > problem.accountCodes.length &&
                  ` e mais ${problem.count - problem.accountCodes.length}`}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
