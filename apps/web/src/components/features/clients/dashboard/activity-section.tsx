/**
 * "Atividade" do painel do cliente (86e3k1q54; faixa compacta desde 08/10/2026):
 * três colunas lado a lado, abaixo do fluxo previsto, que empilham no celular.
 *
 * - **Origem**: uma LINHA de estado ("Omie · Ativa · verificada há 3 dias") e o
 *   link para Contas Bancárias, onde a gestão de origens mora. A seção inteira
 *   (S9) só aparece no painel quando não há origem ativa, e quem decide isso é o
 *   `client-dashboard.tsx`, pelo `origin_status` do detalhe: nunca por
 *   `provider_type`.
 * - **Última conciliação**: do resumo (mês, status, link).
 * - **Glossário**: contagem e versão, quando a lista dele já trouxe os dois.
 */
import { ArrowRight } from 'lucide-react';
import Link from 'next/link';

import {
  connectionRequiresCredentials,
  DEFAULT_PROVIDER_LABEL,
} from '@/lib/api/client-connections';
import type { ClientConnection, ClientSummary, OriginStatus } from '@/lib/contracts';
import { formatCreatedAt, formatLastCheckedAt, formatReferenceMonth } from '@/lib/format';
import { originFixPath } from '@/lib/origin-state';

import { ReconciliationStatusBadge } from '../reconciliation-status-badge';

import { DashboardCard } from './dashboard-card';

/**
 * O texto da linha de origem, com os MESMOS campos que a tabela de origens
 * mostra (tipo, situação, última verificação). Com origem ativa, a conexão
 * descrita é a primeira ativa: é ela que libera as telas.
 */
export function originLine(
  originStatus: OriginStatus,
  connections: readonly ClientConnection[],
): string {
  if (originStatus === 'sem_origem') return 'Nenhuma origem conectada';
  const situation = originStatus === 'ativa' ? 'Ativa' : 'Com erro';
  const connection =
    originStatus === 'ativa'
      ? connections.find((c) => c.status === 'ativa')
      : (connections.find((c) => c.status === 'erro') ?? connections[0]);
  if (connection === undefined) return situation;
  const type = DEFAULT_PROVIDER_LABEL[connection.provider_type] ?? connection.provider_type;
  const checked = connectionRequiresCredentials(connection)
    ? lowerFirst(formatLastCheckedAt(connection.last_checked_at))
    : 'sem credencial';
  return `${type} · ${situation} · ${checked}`;
}

function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1);
}

const LINK_CLASS =
  'text-link inline-flex items-center gap-1 font-medium underline-offset-4 hover:underline';

export function ActivitySection({
  clientId,
  originStatus,
  connections,
  isClosed,
  canManageOrigins,
  latestSession,
  glossary,
}: {
  clientId: string;
  originStatus: OriginStatus;
  connections: readonly ClientConnection[];
  isClosed: boolean;
  /** `manage_client_connections`: muda só o RÓTULO do link ("Gerir" × "Ver"). */
  canManageOrigins: boolean;
  /** `undefined` enquanto o resumo carrega; `null` quando não há conciliação. */
  latestSession: ClientSummary['latestSession'] | undefined;
  /** Contagem e versão do glossário; `undefined` deixa a coluna só com o rótulo. */
  glossary: { total: number; version: number } | undefined;
}) {
  return (
    <DashboardCard title="Atividade" titleId="dashboard-activity-heading" level={2}>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="flex min-w-0 flex-col gap-1.5 text-sm" data-testid="dashboard-origin-line">
          <p className="text-muted-foreground text-xs">Origem</p>
          <p className="font-medium">{originLine(originStatus, connections)}</p>
          {/* Encerrado não tem o que gerir (as conexões saíram no encerramento,
              §4.12): só a linha de estado. */}
          {!isClosed && (
            <Link href={originFixPath(clientId)} className={LINK_CLASS}>
              {canManageOrigins ? 'Gerir origens' : 'Ver origens'}
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Link>
          )}
        </div>

        <div className="flex min-w-0 flex-col gap-1.5 text-sm">
          <p className="text-muted-foreground text-xs">Última conciliação</p>
          {latestSession === undefined ? null : latestSession === null ? (
            <p className="text-muted-foreground">Nenhuma conciliação ainda.</p>
          ) : (
            <>
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
                className={LINK_CLASS}
              >
                Abrir a última conciliação
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Link>
            </>
          )}
        </div>

        <div className="flex min-w-0 flex-col gap-1.5 text-sm">
          <p className="text-muted-foreground text-xs">Glossário</p>
          {glossary !== undefined && (
            <p className="font-medium" data-testid="dashboard-glossary-line">
              {glossary.total} {glossary.total === 1 ? 'termo' : 'termos'} · versão{' '}
              {glossary.version}
            </p>
          )}
        </div>
      </div>
    </DashboardCard>
  );
}
