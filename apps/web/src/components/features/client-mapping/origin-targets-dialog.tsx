'use client';

/**
 * "Criar alvos a partir das contas de demonstrativo deste cliente" (86e3n70pn,
 * bloco B).
 *
 * Organização nova nasce com o demonstrativo SEM alvo nenhum, e a herança ("Iniciar
 * de-para") casa o código da conta de demonstrativo de cada categoria do Omie com o
 * código de um alvo: sem alvo, herda zero. Este diálogo mostra a PRÉVIA que o
 * servidor monta (as contas de demonstrativo distintas das categorias ativas do
 * cliente, com o nome que o Omie dá a cada uma e se o catálogo já tem o alvo) e,
 * só depois de a pessoa CONFIRMAR, cria os faltantes pelo lote do catálogo.
 *
 * É dado do Omie virando configuração da ORGANIZAÇÃO (vale para todos os clientes
 * dela), por decisão explícita de quem tem `manage_mapping_catalog`; nunca
 * automático. Quem chama esconde o botão de quem não tem a permissão (o servidor
 * nega a prévia com 403 de qualquer jeito).
 */

import { Loader2, Wand2 } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';
import { toast } from 'sonner';

import { chartOfAccountsPath } from '@/components/features/navigation/nav-items';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { ScrollRegion } from '@/components/ui/scroll-region';
import { useCreateMappingTargets, useOriginTargetsPreview } from '@/hooks/use-client-mapping';
import { ApiError } from '@/lib/api/client';
import type { MappingDestination, OriginTargetCandidate } from '@/lib/contracts';

interface OriginTargetsActionProps {
  clientId: string;
  destination: MappingDestination;
  /** Rótulo do botão — o aviso de catálogo vazio usa o texto longo. */
  label?: string;
  variant?: 'default' | 'outline' | 'secondary';
}

// O rótulo longo do aviso quebra linha em 390px em vez de sair pela borda (o
// `Button` é `whitespace-nowrap` por padrão).
const WRAPPING_LABEL = 'h-auto min-h-9 whitespace-normal py-1.5 text-left';

export function OriginTargetsAction({
  clientId,
  destination,
  label = 'Criar alvos a partir da origem',
  variant = 'outline',
}: OriginTargetsActionProps) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        type="button"
        variant={variant}
        size="sm"
        className={WRAPPING_LABEL}
        onClick={() => setOpen(true)}
      >
        <Wand2 className="h-4 w-4" aria-hidden="true" />
        {label}
      </Button>
      {open && (
        <OriginTargetsDialog
          open={open}
          onOpenChange={setOpen}
          clientId={clientId}
          destination={destination}
        />
      )}
    </>
  );
}

function OriginTargetsDialog({
  open,
  onOpenChange,
  clientId,
  destination,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
  destination: MappingDestination;
}) {
  const preview = useOriginTargetsPreview(clientId, destination.type, { enabled: open });
  const createMutation = useCreateMappingTargets(destination.id);
  const data = preview.data;
  const creatable = (data?.candidates ?? []).filter((c) => c.creatable);

  async function handleConfirm() {
    try {
      const created = await createMutation.mutateAsync({
        targets: creatable.map((c) => ({ code: c.code, name: c.name ?? '' })),
      });
      toast.success(
        `${created.length} ${created.length === 1 ? 'alvo criado' : 'alvos criados'} em ${destination.name}. Agora use "Iniciar de-para" para herdar as decisões.`,
      );
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.userMessage : 'Não foi possível criar os alvos.');
      // O catálogo pode ter mudado em outra aba (409): a prévia volta atualizada.
      void preview.refetch();
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Criar alvos a partir da origem</DialogTitle>
          <DialogDescription>
            As contas de demonstrativo que as categorias do Omie deste cliente já declaram viram
            alvos de &quot;{destination.name}&quot;. Os alvos entram no catálogo da organização e
            valem para todos os clientes dela.
          </DialogDescription>
        </DialogHeader>

        {preview.isLoading ? (
          <p className="text-muted-foreground flex items-center gap-2 text-sm" role="status">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Lendo as contas de demonstrativo do cliente…
          </p>
        ) : preview.isError ? (
          <p role="alert" className="text-destructive text-sm">
            {preview.error instanceof ApiError
              ? preview.error.userMessage
              : 'Não foi possível ler as contas de demonstrativo do cliente.'}
          </p>
        ) : data?.state === 'sem_plano_de_contas' ? (
          <div role="status" className="bg-muted space-y-2 rounded-lg p-3 text-sm">
            <p className="font-medium">
              As categorias do Omie deste cliente não foram sincronizadas
            </p>
            <p className="text-muted-foreground">
              É nelas que vem a conta de demonstrativo de cada categoria. Sincronize em
              &quot;Categorias do Omie&quot; e volte aqui.
            </p>
            <Button asChild variant="outline" size="sm">
              <Link href={chartOfAccountsPath(clientId)}>Ir para Categorias do Omie</Link>
            </Button>
          </div>
        ) : data?.state === 'destino_sem_heranca' ? (
          <p role="status" className="text-muted-foreground text-sm">
            Só o demonstrativo contábil tem alvos declarados pela origem.
          </p>
        ) : data && data.candidates.length === 0 ? (
          <p role="status" className="text-muted-foreground text-sm">
            Nenhuma categoria ativa deste cliente declara conta de demonstrativo no Omie. Cadastre
            os alvos à mão em Configurações → Destinos do de-para.
          </p>
        ) : data ? (
          <div className="space-y-3">
            <p className="text-sm" role="status" data-testid="origin-targets-summary">
              {creatable.length === 0
                ? 'Todas as contas de demonstrativo deste cliente já estão no catálogo.'
                : `${creatable.length} ${creatable.length === 1 ? 'conta nova será criada' : 'contas novas serão criadas'} como alvo; ${data.candidates.length - creatable.length} já ${data.candidates.length - creatable.length === 1 ? 'existe ou fica de fora' : 'existem ou ficam de fora'}.`}
            </p>
            {!data.namesResolved && (
              <p className="bg-warning-muted text-warning ring-warning/30 rounded-md p-2 text-sm ring-1 ring-inset">
                O Omie não respondeu agora com o nome de algumas contas: elas ficam de fora deste
                lote. Tente de novo em alguns minutos.
              </p>
            )}
            <ScrollRegion
              label="Contas de demonstrativo da origem"
              className="max-h-72 rounded-md border"
            >
              <ul className="divide-y text-sm">
                {data.candidates.map((candidate) => (
                  <CandidateRow key={candidate.code} candidate={candidate} />
                ))}
              </ul>
            </ScrollRegion>
          </div>
        ) : null}

        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={createMutation.isPending}
          >
            Cancelar
          </Button>
          <Button
            type="button"
            onClick={() => void handleConfirm()}
            disabled={createMutation.isPending || creatable.length === 0}
          >
            {createMutation.isPending && (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            )}
            {creatable.length > 1 ? `Criar ${creatable.length} alvos` : 'Criar alvo'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CandidateRow({ candidate }: { candidate: OriginTargetCandidate }) {
  const status = candidate.exists
    ? candidate.active
      ? 'Já no catálogo'
      : 'Já no catálogo (inativo)'
    : candidate.creatable
      ? 'Será criado'
      : candidate.name === null
        ? 'Sem nome agora'
        : 'Não cabe no catálogo';
  return (
    <li className="flex items-start justify-between gap-3 px-3 py-2">
      <div className="min-w-0">
        <span className="block font-medium tabular-nums">{candidate.code}</span>
        <span className="text-muted-foreground block text-xs">
          {candidate.name ?? 'Nome indisponível agora'} ·{' '}
          {candidate.categories === 1 ? '1 categoria' : `${candidate.categories} categorias`}
        </span>
      </div>
      <span
        className={
          candidate.creatable
            ? 'text-success shrink-0 text-xs font-medium'
            : 'text-muted-foreground shrink-0 text-xs'
        }
      >
        {status}
      </span>
    </li>
  );
}
