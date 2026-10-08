/**
 * "Origem e atividade" do painel do cliente (86e3k1q54): a seção de origens que
 * já existia (S9, sem redesenho), a conciliação mais recente (do resumo) e o
 * glossário, quando a lista dele já trouxer contagem e versão.
 */
import { ArrowRight } from 'lucide-react';
import Link from 'next/link';

import type { ClientSummary, OriginStatus } from '@/lib/contracts';
import { formatCreatedAt, formatReferenceMonth } from '@/lib/format';

import { ClientConnectionsSection } from '../connections/client-connections-section';
import { ReconciliationStatusBadge } from '../reconciliation-status-badge';

import { DashboardCard } from './dashboard-card';

export function ActivitySection({
  clientId,
  originStatus,
  isClosed,
  latestSession,
  glossary,
}: {
  clientId: string;
  originStatus: OriginStatus;
  isClosed: boolean;
  /** `undefined` enquanto o resumo carrega; `null` quando não há conciliação. */
  latestSession: ClientSummary['latestSession'] | undefined;
  /** Contagem e versão do glossário; `undefined` omite a linha. */
  glossary: { total: number; version: number } | undefined;
}) {
  return (
    <div className="flex flex-col gap-3">
      <ClientConnectionsSection
        clientId={clientId}
        originStatus={originStatus}
        isClosed={isClosed}
      />
      <DashboardCard title="Atividade" titleId="dashboard-card-activity">
        {latestSession === undefined ? null : latestSession === null ? (
          <p className="text-muted-foreground text-sm">Nenhuma conciliação ainda.</p>
        ) : (
          <div className="flex flex-col gap-2 text-sm">
            <p className="text-muted-foreground text-xs">Última conciliação</p>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">
                {formatReferenceMonth(latestSession.referenceMonth)}
              </span>
              <ReconciliationStatusBadge status={latestSession.status} />
            </div>
            <p className="text-muted-foreground text-xs">
              Criada em {formatCreatedAt(latestSession.createdAt)}
            </p>
            <Link
              href={`/clientes/${clientId}/conciliacao/${latestSession.id}`}
              className="text-link inline-flex items-center gap-1 font-medium underline-offset-4 hover:underline"
            >
              Abrir a última conciliação
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Link>
          </div>
        )}
        {glossary !== undefined && (
          <p className="border-t pt-2 text-sm" data-testid="dashboard-glossary-line">
            <span className="text-muted-foreground">Glossário: </span>
            {glossary.total} {glossary.total === 1 ? 'termo' : 'termos'} · versão {glossary.version}
          </p>
        )}
      </DashboardCard>
    </div>
  );
}
