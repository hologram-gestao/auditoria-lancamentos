'use client';

/**
 * "O flag procedia?" — julgamento do revisor (Sprint 6 / R4 — FRONT 06.7).
 *
 * É a ação que alimenta a métrica de outcome da sprint: o backend emite
 * `flag_revisado` a cada MUDANÇA real de veredito (reenviar o mesmo valor não
 * duplica), e a fórmula é "flags improcedentes ÷ flags emitidas".
 *
 * **Eixo diferente de "resolvida".** "Resolvida" quer dizer que alguém agiu
 * sobre a anomalia; "procedente/improcedente" quer dizer se ela *devia ter
 * sido levantada*. Por isso este controle é uma coluna própria e não um estado
 * a mais do `StatusPill` — misturar os dois apagaria a métrica. O PATCH manda
 * **só** `review_verdict`: omitir `resolved` significa "não mexa nele" (o
 * contrato da BACK 06.5 tornou os dois opcionais).
 *
 * **Só flag da Camada 1.** O servidor aceita veredito apenas em
 * `qualificacao_suspeita`/`qualificacao_incoerente` e devolve 400 nos demais —
 * então o controle nem aparece nos outros tipos, em vez de oferecer um botão
 * que erra. Quem decide continua sendo o backend.
 *
 * **Três estados, todos VISÍVEIS.** "Não avaliado" é um rótulo de verdade, não
 * a ausência dos outros dois: sem ele o revisor não distingue "ainda não olhei"
 * de "olhei e achei procedente". O estado é comunicado por texto + `aria-pressed`
 * — nunca só por cor (as duas variantes de botão diferem, mas a cor é reforço).
 */

import { Loader2, ThumbsDown, ThumbsUp } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { usePatchAnomaly } from '@/hooks/use-reconciliations';
import { ApiError } from '@/lib/api/client';
import type { AnomalyItem, AnomalyReviewVerdict } from '@/lib/api/reconciliations';
import { cn } from '@/lib/utils';

/**
 * Códigos que o servidor aceita julgar — os MESMOS de
 * `QUALIFICATION_FLAG_CODES` (`review/service.py`). `padrao_quebrado` e
 * `valor_outlier` ficam de fora de propósito: eles não entram no denominador
 * da métrica, e julgá-los inflaria só o numerador.
 */
export const REVIEWABLE_FLAG_CODES = ['qualificacao_suspeita', 'qualificacao_incoerente'] as const;

const REVIEWABLE_SET = new Set<string>(REVIEWABLE_FLAG_CODES);

export function acceptsReviewVerdict(anomaly: AnomalyItem): boolean {
  return REVIEWABLE_SET.has(anomaly.anomaly_type.code);
}

const VERDICT_LABELS: Record<AnomalyReviewVerdict, string> = {
  procedente: 'Procedente',
  improcedente: 'Improcedente',
};

interface AnomalyVerdictControlProps {
  sessionId: string;
  anomaly: AnomalyItem;
  /**
   * Matriz do R4 (`review_export`), resolvida pelo consumidor com
   * `hasPermission` — nunca `role === '...'` aqui dentro.
   */
  canReview: boolean;
}

export function AnomalyVerdictControl({
  sessionId,
  anomaly,
  canReview,
}: AnomalyVerdictControlProps) {
  const mutation = usePatchAnomaly(sessionId);
  const current = anomaly.review_verdict ?? null;
  const isPending = mutation.isPending;

  async function apply(verdict: AnomalyReviewVerdict) {
    // Reenviar o mesmo valor é inócuo no servidor, mas gasta um request e um
    // refetch da lista — e o operador clica de novo por reflexo.
    if (current === verdict || isPending) return;
    try {
      await mutation.mutateAsync({
        anomalyId: anomaly.id,
        // SÓ o veredito: `resolved` omitido = "não mexa nele".
        payload: { review_verdict: verdict },
      });
      toast.success(
        verdict === 'procedente'
          ? 'Flag marcado como procedente.'
          : 'Flag marcado como improcedente.',
      );
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível registrar o veredito.',
      );
    }
  }

  if (!canReview) {
    return <VerdictState current={current} />;
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <VerdictState current={current} />
      <div className="flex items-center gap-1" aria-busy={isPending}>
        <VerdictButton
          verdict="procedente"
          active={current === 'procedente'}
          disabled={isPending}
          pending={isPending}
          anomalyName={anomaly.anomaly_type.name}
          onClick={() => void apply('procedente')}
        />
        <VerdictButton
          verdict="improcedente"
          active={current === 'improcedente'}
          disabled={isPending}
          pending={isPending}
          anomalyName={anomaly.anomaly_type.name}
          onClick={() => void apply('improcedente')}
        />
      </div>
    </div>
  );
}

/**
 * O estado atual em TEXTO. `aria-live="polite"` porque a mudança acontece longe
 * do foco (o botão continua sendo o botão) — sem isso, quem usa leitor de tela
 * clica e não recebe confirmação alguma.
 */
function VerdictState({ current }: { current: AnomalyReviewVerdict | null }) {
  if (current === null) {
    return (
      <span className="text-muted-foreground text-xs" aria-live="polite">
        Não avaliado
      </span>
    );
  }
  return (
    <span
      aria-live="polite"
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
        current === 'procedente'
          ? 'bg-warning-muted text-warning ring-warning/30'
          : 'bg-info-muted text-info ring-info/30',
      )}
    >
      {VERDICT_LABELS[current]}
    </span>
  );
}

interface VerdictButtonProps {
  verdict: AnomalyReviewVerdict;
  active: boolean;
  disabled: boolean;
  pending: boolean;
  anomalyName: string;
  onClick: () => void;
}

function VerdictButton({
  verdict,
  active,
  disabled,
  pending,
  anomalyName,
  onClick,
}: VerdictButtonProps) {
  const Icon = verdict === 'procedente' ? ThumbsUp : ThumbsDown;
  return (
    <Button
      type="button"
      size="sm"
      variant={active ? 'default' : 'outline'}
      // `aria-pressed` é o que carrega o estado para a tecnologia assistiva —
      // a variante do botão é só o reforço visual.
      aria-pressed={active}
      aria-label={`Marcar "${anomalyName}" como ${VERDICT_LABELS[verdict].toLowerCase()}`}
      disabled={disabled}
      onClick={onClick}
    >
      {pending && active ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
      ) : (
        <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      )}
      {VERDICT_LABELS[verdict]}
    </Button>
  );
}
