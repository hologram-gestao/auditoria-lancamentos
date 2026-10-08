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
  return (
    <div
      data-testid="dashboard-no-reconciliations"
      data-state={state}
      className="flex flex-col items-start gap-3"
    >
      <p className="text-muted-foreground text-sm">
        {state === 'encerrado'
          ? 'Este cliente foi encerrado e não tem conciliações. O histórico fica disponível só para leitura.'
          : state === 'arquivo'
            ? 'Este cliente não concilia: os lançamentos dele entram pelo envio do arquivo do mês e são classificados no de-para.'
            : state === 'sem-origem'
              ? 'Este cliente ainda não tem conciliações. Para conciliar, ele precisa de uma origem conectada e ativa: o estado da origem está em "Origem e atividade".'
              : 'Este cliente ainda não tem conciliações. Comece pela lista de conciliações.'}
      </p>
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
    </div>
  );
}
