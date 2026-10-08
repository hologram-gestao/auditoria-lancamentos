/**
 * Cliente sem nenhuma conciliação (86e3g9uku), dentro do card "Conciliações do
 * mês" do painel (86e3k1q54).
 *
 * Quem decide o estado é `reconciliationCreation` — a MESMA decisão que a lista
 * consulta para esconder a criação. Perguntar diferente aqui faria as duas telas
 * discordarem sobre o mesmo cliente, e o link prometeria uma ação que o destino
 * não tem.
 */
import { ArrowRight } from 'lucide-react';
import Link from 'next/link';

import { fileOriginPath, reconciliationsPath } from '@/components/features/navigation/nav-items';
import { reconciliationCreation } from '@/components/features/reconciliations/create/creation-availability';
import { EmptyState } from '@/components/shared/empty-state';
import { ReconciliationsVignette } from '@/components/shared/vignettes';
import { Button } from '@/components/ui/button';
import type { ClientDetail } from '@/lib/api/clients';
import { originIsFileBased } from '@/lib/origin-capabilities';

export function NoReconciliations({
  clientId,
  detail,
}: {
  clientId: string;
  detail: Pick<ClientDetail, 'closed_at' | 'origin_status' | 'connections'>;
}) {
  const { isClosed, canCreate } = reconciliationCreation(detail);
  const fileOrigin = originIsFileBased(detail.connections ?? []);
  const state = isClosed
    ? 'encerrado'
    : fileOrigin
      ? 'arquivo'
      : canCreate
        ? 'pronto'
        : 'sem-origem';
  // Vinheta e moldura do `EmptyState` (86e3h57b5), sem borda: o card do painel já é a
  // moldura. Os textos e as ações não mudaram.
  return (
    <EmptyState
      framed={false}
      vignette={<ReconciliationsVignette />}
      data-testid="dashboard-no-reconciliations"
      data-state={state}
      description={
        state === 'encerrado'
          ? 'Este cliente foi encerrado e não tem conciliações. O histórico fica disponível só para leitura.'
          : state === 'arquivo'
            ? 'Este cliente não concilia: os lançamentos dele entram pelo envio do arquivo do mês e são classificados no de-para.'
            : state === 'sem-origem'
              ? 'Este cliente ainda não tem conciliações. Para conciliar, ele precisa de uma origem conectada e ativa: o estado da origem está em "Origem e atividade".'
              : 'Este cliente ainda não tem conciliações. Comece pela lista de conciliações.'
      }
      action={
        <>
          {state === 'pronto' && (
            <Button asChild size="sm">
              {/* Rótulo honesto: o link leva à LISTA, onde a gaveta de criação vive. */}
              <Link href={reconciliationsPath(clientId)}>
                Ir para conciliações
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Link>
            </Button>
          )}
          {state === 'arquivo' && (
            <Button asChild variant="outline" size="sm">
              <Link href={fileOriginPath(clientId)}>
                Ir para Origem por arquivo
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Link>
            </Button>
          )}
        </>
      }
    />
  );
}
