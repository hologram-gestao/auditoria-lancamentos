/**
 * Vigência por competência (Sprint 12 / R4) — as peças que TODA escrita do
 * de-para compartilha: a decisão individual, o lote das herdadas, o "Iniciar
 * de-para" e a importação.
 *
 * O R4 exige três coisas da tela, e elas moram aqui para não divergirem entre os
 * quatro fluxos:
 *
 *   1. deixar EXPLÍCITO que a alteração cria vigência nova, e a partir de quando
 *      (`VigenciaExplanation`);
 *   2. pedir a competência de início com padrão = corrente (o campo, com o valor
 *      inicial vindo de quem chama — a competência do SERVIDOR);
 *   3. traduzir os dois 409 de vigência em ESTADO, não em toast: retroativa sem
 *      materialização lista as competências afetadas e pede confirmação;
 *      retroativa sobre competência materializada é recusa, com as competências
 *      nomeadas (`readVigenciaConflict` + `VigenciaConflictNotice`).
 */
import { AlertTriangle, Lock } from 'lucide-react';

import { ApiError } from '@/lib/api/client';
import { isBefore, isCompetence, parseCompetenceList, previousCompetence } from '@/lib/competence';
import { formatReferenceMonth } from '@/lib/format';

export type VigenciaConflict =
  | { kind: 'retroactive'; competences: string[] }
  | { kind: 'materialized'; competences: string[]; message: string };

/** Lê um erro da API como conflito de vigência; qualquer outro erro → `null`. */
export function readVigenciaConflict(error: unknown): VigenciaConflict | null {
  if (!(error instanceof ApiError)) return null;
  if (error.code === 'RETROATIVA_REQUER_CONFIRMACAO') {
    return { kind: 'retroactive', competences: parseCompetenceList(error.details.competences) };
  }
  if (error.code === 'COMPETENCIA_MATERIALIZADA') {
    return {
      kind: 'materialized',
      competences: parseCompetenceList(error.details.competences),
      message: error.userMessage,
    };
  }
  return null;
}

function monthList(competences: string[]): string {
  return competences.map((item) => formatReferenceMonth(item)).join(', ');
}

/**
 * O texto que diz o que a gravação FAZ. `hasCurrentDecision=false` é a primeira
 * decisão da categoria: não há vigência anterior para "continuar valendo".
 */
export function VigenciaExplanation({
  effectiveFrom,
  serverCompetence,
  hasCurrentDecision,
}: {
  effectiveFrom: string;
  serverCompetence: string;
  hasCurrentDecision: boolean;
}) {
  if (!isCompetence(effectiveFrom)) return null;
  const retroactive = isBefore(effectiveFrom, serverCompetence);
  return (
    <div
      className="bg-info-muted text-info ring-info/30 space-y-1 rounded-lg p-3 text-sm ring-1 ring-inset"
      data-testid="vigencia-explanation"
    >
      <p className="font-medium">
        Cria uma vigência nova a partir de {formatReferenceMonth(effectiveFrom)}.
      </p>
      <p>
        {hasCurrentDecision
          ? `A decisão atual continua valendo até ${formatReferenceMonth(previousCompetence(effectiveFrom))}; nada é sobrescrito.`
          : 'Competências anteriores continuam sem esta decisão.'}
      </p>
      {retroactive && (
        <p>
          O início é anterior à competência corrente ({formatReferenceMonth(serverCompetence)}): as
          competências passadas afetadas serão listadas para você confirmar.
        </p>
      )}
    </div>
  );
}

/** O 409 de vigência como estado dentro do diálogo que o provocou. */
export function VigenciaConflictNotice({ conflict }: { conflict: VigenciaConflict }) {
  if (conflict.kind === 'retroactive') {
    return (
      <div
        role="alert"
        className="bg-warning-muted text-warning ring-warning/30 space-y-1 rounded-lg p-3 text-sm ring-1 ring-inset"
      >
        <p className="flex items-center gap-1.5 font-medium">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
          Alteração retroativa
        </p>
        <p>
          Esta alteração passa a valer também para{' '}
          {conflict.competences.length > 0
            ? monthList(conflict.competences)
            : 'competências passadas'}
          . Nenhuma delas foi materializada ainda. Confirme para aplicar.
        </p>
      </div>
    );
  }
  return (
    <div
      role="alert"
      className="bg-destructive-muted text-destructive ring-destructive/30 space-y-1 rounded-lg p-3 text-sm ring-1 ring-inset"
    >
      <p className="flex items-center gap-1.5 font-medium">
        <Lock className="h-4 w-4 shrink-0" aria-hidden="true" />
        Competência já materializada
      </p>
      <p>
        {conflict.message}
        {conflict.competences.length > 0 && ` Materializadas: ${monthList(conflict.competences)}.`}
      </p>
    </div>
  );
}
