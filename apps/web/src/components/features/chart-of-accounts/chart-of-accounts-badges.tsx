/**
 * Badges do plano de contas do cliente (Sprint 10 / R3).
 *
 * Mesmo desenho do `glossary-badges` (ADR-007-FE): cor **só por token
 * semântico** (`success`, `warning`, `muted`), nunca `emerald-50`/`zinc-100` da
 * paleta crua — que não tem par nos temas escuro e Hologram sem duplicar
 * classe. Nada aqui comunica estado por `opacity`.
 */
import type { ChartOfAccountsStatus } from '@/lib/contracts';
import { cn } from '@/lib/utils';

const baseBadge =
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

export const CHART_STATUS_LABELS: Record<ChartOfAccountsStatus, string> = {
  ativa: 'Ativa',
  inativa: 'Inativa',
  ausente_na_origem: 'Ausente na origem',
};

const STATUS_CLASSES: Record<ChartOfAccountsStatus, string> = {
  ativa: 'bg-success-muted text-success ring-success/30',
  inativa: 'bg-muted text-muted-foreground ring-border',
  // "Ausente na origem" é AVISO, não erro: a categoria sumiu do cadastro e a
  // linha ficou de propósito (pode haver de-para apontando para ela). Vermelho
  // diria "conserte isto", e não há o que consertar aqui.
  ausente_na_origem: 'bg-warning-muted text-warning ring-warning/30',
};

const NEUTRAL = 'bg-muted text-muted-foreground ring-border';

/**
 * `status` é enum FECHADO no contrato, mas chega do servidor em runtime — um
 * valor fora da whitelist cai num neutro com o texto cru: visível, e sem
 * derrubar a listagem inteira por causa de uma linha.
 */
export function ChartStatusBadge({ status }: { status: string }) {
  const known = status as ChartOfAccountsStatus;
  return (
    <span className={cn(baseBadge, STATUS_CLASSES[known] ?? NEUTRAL)}>
      {CHART_STATUS_LABELS[known] ?? status}
    </span>
  );
}

/**
 * As três marcações que a origem declara. Elas existem na tela porque são a
 * EXPLICAÇÃO de "sem destino declarado": na amostra real, as categorias sem
 * conta de demonstrativo são exatamente as transferências e as totalizadoras —
 * que, por definição contábil, não têm destino próprio. Sem elas, a coluna
 * vazia parece pendência.
 */
export function ChartFlagBadges({
  totalizadora,
  transferencia,
  naoExibir,
}: {
  totalizadora: boolean;
  transferencia: boolean;
  naoExibir: boolean;
}) {
  const flags: string[] = [];
  if (totalizadora) flags.push('Totalizadora');
  if (transferencia) flags.push('Transferência');
  if (naoExibir) flags.push('Não exibir');
  if (flags.length === 0) return null;
  return (
    <>
      {flags.map((flag) => (
        <span key={flag} className={cn(baseBadge, NEUTRAL)}>
          {flag}
        </span>
      ))}
    </>
  );
}
