/**
 * Badges do glossário do tenant (Sprint 6 / R2).
 *
 * Mesmo desenho do `client-user-badges` (ADR-007-FE): cor **só por token
 * semântico** (`info`, `warning`, `destructive`, `muted`), nunca `blue-50`/
 * `zinc-100` da paleta crua — que muda de significado quando a marca troca e
 * não tem par no tema escuro sem duplicar classe.
 *
 * O par `bg-*-muted` + `text-*` é o mesmo já travado pelo teste de contraste de
 * tokens; e nada aqui usa `opacity` para comunicar estado (foi o que derrubou
 * três elementos abaixo de 4.5:1 na Sprint 5).
 */
import { cn } from '@/lib/utils';
import { GLOSSARY_KIND_LABELS, type GlossaryKindFormValue } from '@/lib/validation/glossary';

const baseBadge =
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

const KIND_CLASSES: Record<GlossaryKindFormValue, string> = {
  categoria: 'bg-info-muted text-info ring-info/30',
  fornecedor: 'bg-warning-muted text-warning ring-warning/30',
  regra: 'bg-muted text-muted-foreground ring-border',
};

/**
 * `kind` é enum FECHADO no contrato, mas chega do servidor em runtime — um
 * valor fora da whitelist cai num neutro com o texto cru: visível e sem
 * quebrar a listagem inteira por causa de uma linha.
 */
export function GlossaryKindBadge({ kind }: { kind: string }) {
  const known = kind as GlossaryKindFormValue;
  const label = GLOSSARY_KIND_LABELS[known] ?? kind;
  const classes = KIND_CLASSES[known] ?? 'bg-muted text-muted-foreground ring-border';
  return <span className={cn(baseBadge, classes)}>{label}</span>;
}

/**
 * Entrada cujo texto não decifrou (`decryptFailed` do contrato). O backend já
 * devolve `[indecifrável]` no campo em vez de string vazia; a badge existe para
 * o estado não se comunicar **só** pelo texto do meio da tabela — e para deixar
 * claro que é falha de leitura do dado, não conteúdo cadastrado assim.
 */
export function GlossaryUndecipherableBadge() {
  return (
    <span
      className={cn(baseBadge, 'bg-destructive/10 text-destructive ring-destructive/30')}
      title="O texto desta entrada não pôde ser decifrado."
    >
      Indecifrável
    </span>
  );
}
