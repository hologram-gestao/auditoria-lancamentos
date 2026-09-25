/**
 * Badges e rótulos da carteira de títulos (Sprint 11 / R4).
 *
 * Mesmo desenho do `chart-of-accounts-badges` (ADR-007-FE): cor **só por token
 * semântico** (`success`, `warning`, `destructive`, `muted`), nunca
 * `emerald-50`/`zinc-100` da paleta crua — que não tem par nos temas escuro e
 * Hologram sem duplicar classe. Nada aqui comunica estado por `opacity`.
 *
 * ⚠️ **O balde NÃO é redundante com o status.** `status` diz se o título ainda
 * está em aberto; o balde diz há quanto tempo venceu. Um título `liquidado`
 * volta com `bucket: null` do servidor — a linha fica (pode haver contexto
 * apontando para ela), mas não pertence a balde nenhum.
 */
import type { AgingBucket, TitleStatus, TitleType } from '@/lib/contracts';
import { cn } from '@/lib/utils';

const baseBadge =
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

const NEUTRAL = 'bg-muted text-muted-foreground ring-border';

export const TITLE_TYPE_LABELS: Record<TitleType, string> = {
  a_pagar: 'A pagar',
  a_receber: 'A receber',
};

export const TITLE_STATUS_LABELS: Record<TitleStatus, string> = {
  em_aberto: 'Em aberto',
  liquidado: 'Liquidado',
  ausente_na_origem: 'Ausente na origem',
};

const STATUS_CLASSES: Record<TitleStatus, string> = {
  em_aberto: 'bg-info-muted text-info ring-info/30',
  // Liquidado é DESFECHO bom: o título saiu da carteira porque foi pago ou
  // recebido. Neutro o faria parecer "sem informação".
  liquidado: 'bg-success-muted text-success ring-success/30',
  // "Ausente na origem" é AVISO, não erro: o título sumiu do cadastro (em geral
  // reemissão) e a linha ficou de propósito. Vermelho diria "conserte isto", e
  // não há o que consertar aqui.
  ausente_na_origem: 'bg-warning-muted text-warning ring-warning/30',
};

/** Rótulos dos baldes — os mesmos da segmentação que o escritório já usa à mão. */
export const BUCKET_LABELS: Record<AgingBucket, string> = {
  a_vencer: 'A vencer',
  '1_30': '1 a 30 dias',
  '31_60': '31 a 60 dias',
  '61_90': '61 a 90 dias',
  '90_mais': '90+ dias',
};

/**
 * A gravidade CRESCE com o balde, e é a única coisa que a cor comunica aqui:
 * `a_vencer` é neutro (não venceu nada), e 90+ é destrutivo — é o número que a
 * reunião de acompanhamento procura primeiro.
 */
const BUCKET_CLASSES: Record<AgingBucket, string> = {
  a_vencer: NEUTRAL,
  '1_30': 'bg-warning-muted text-warning ring-warning/30',
  '31_60': 'bg-warning-muted text-warning ring-warning/30',
  // `bg-destructive/10` reprovou no gate (4,22:1 no escuro, 4,42:1 no Hologram,
  // AA pede 4,5:1 em 12px): o alfa mistura o vermelho com o que estiver embaixo,
  // e numa linha em hover (`hover:bg-muted/50`) isso apaga o contraste (86e3dxund). `destructive-muted` é o token de FUNDO de badge, opaco e
  // calibrado nos três temas — a mesma convenção que `warning`/`success`/`info`
  // acima já seguem.
  '61_90': 'bg-destructive-muted text-destructive ring-destructive/30',
  '90_mais': 'bg-destructive-muted text-destructive ring-destructive/30',
};

/**
 * `status` é enum FECHADO no contrato, mas chega do servidor em runtime — um
 * valor fora da whitelist cai num neutro com o texto cru: visível, e sem
 * derrubar a carteira inteira por causa de uma linha.
 */
export function TitleStatusBadge({ status }: { status: string }) {
  const known = status as TitleStatus;
  return (
    <span className={cn(baseBadge, STATUS_CLASSES[known] ?? NEUTRAL)}>
      {TITLE_STATUS_LABELS[known] ?? status}
    </span>
  );
}

export function TitleTypeBadge({ titleType }: { titleType: string }) {
  const known = titleType as TitleType;
  return (
    <span className={cn(baseBadge, NEUTRAL, 'whitespace-nowrap')}>
      {TITLE_TYPE_LABELS[known] ?? titleType}
    </span>
  );
}

/** `null` = título que já saiu do aberto: não pertence a balde nenhum. */
export function TitleBucketBadge({ bucket }: { bucket: string | null }) {
  if (bucket === null) return null;
  const known = bucket as AgingBucket;
  return (
    <span className={cn(baseBadge, BUCKET_CLASSES[known] ?? NEUTRAL, 'whitespace-nowrap')}>
      {BUCKET_LABELS[known] ?? bucket}
    </span>
  );
}
