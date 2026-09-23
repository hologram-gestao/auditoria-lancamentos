/**
 * Selos das origens do cliente (Sprint 9 / R3 · R7).
 *
 * Estado por **token semântico**, nunca por `opacity` na linha: o ADR-007-FE
 * pegou exatamente isso em seis tabelas — `opacity-60` derruba o contraste da
 * linha inteira e o estado fica ilegível justamente em quem precisa dele.
 *
 * `inativa` e `erro` NÃO são a mesma coisa para o usuário (desligada de
 * propósito × credencial recusada), então têm selos diferentes — mas as duas
 * caem no mesmo 409 `ORIGEM_COM_ERRO` do servidor, porque a ação é a mesma:
 * reconectar.
 */

import type { ConnectionStatus, OriginStatus } from '@/lib/contracts';
import { cn } from '@/lib/utils';

const baseBadge =
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

const CONNECTION_STATUS_STYLE: Record<ConnectionStatus, { label: string; className: string }> = {
  ativa: { label: 'Ativa', className: 'bg-success-muted text-success ring-success/30' },
  inativa: { label: 'Inativa', className: 'bg-muted text-muted-foreground ring-border' },
  erro: {
    label: 'Com erro',
    className: 'bg-destructive-muted text-destructive ring-destructive/30',
  },
};

export function ConnectionStatusBadge({ status }: { status: ConnectionStatus }) {
  // `status` chega do servidor: valor fora do enum vira selo neutro com o
  // texto cru, em vez de derrubar a linha com um `undefined.label`.
  const style = CONNECTION_STATUS_STYLE[status] as { label: string; className: string } | undefined;
  if (style === undefined) {
    return (
      <span className={cn(baseBadge, 'bg-muted text-muted-foreground ring-border')}>{status}</span>
    );
  }
  return <span className={cn(baseBadge, style.className)}>{style.label}</span>;
}

const ORIGIN_STATUS_STYLE: Record<OriginStatus, { label: string; className: string }> = {
  sem_origem: { label: 'Sem origem', className: 'bg-warning-muted text-warning ring-warning/30' },
  ativa: { label: 'Origem ativa', className: 'bg-success-muted text-success ring-success/30' },
  erro: {
    label: 'Origem com erro',
    className: 'bg-destructive-muted text-destructive ring-destructive/30',
  },
};

/**
 * Selo do estado da ORIGEM do cliente — usado na lista de clientes e no painel.
 *
 * Carrega `aria-label` porque na lista ele vive numa célula junto do nome: o
 * leitor de tela precisa ouvir "Sem origem conectada", não só "Sem origem"
 * colado no nome do cliente.
 */
export function OriginStatusBadge({ status }: { status: OriginStatus }) {
  const style = ORIGIN_STATUS_STYLE[status] as { label: string; className: string } | undefined;
  if (style === undefined) return null;
  return (
    <span className={cn(baseBadge, style.className)} aria-label={`${style.label} neste cliente`}>
      {style.label}
    </span>
  );
}
