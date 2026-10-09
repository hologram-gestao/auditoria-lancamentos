/**
 * Contadores por situação da aba Decisões do de-para (86e3f55bd).
 *
 * "Quantas herdadas faltam confirmar, quantas estão sem decisão" — antes a
 * pessoa só sabia indo à Prévia, que é por competência.
 *
 * ⚠️ **Os números vêm do SERVIDOR**, no envelope da lista (`counts`), contados
 * sobre o universo INTEIRO do destino na competência corrente: são os mesmos em
 * qualquer página, situação e código. Este componente não conta nada.
 *
 * **Cada valor é um botão que FILTRA a lista**, no desenho dos totais da
 * carteira (`client-titles-summary.tsx`): `situation` na URL, aplicada no
 * servidor. `mappingCountFilterSituation` é a fonte única do mapeamento e
 * decide também quando um botão está ATIVO; "Categorias" é o "sem recorte".
 *
 * `<dl>` com o botão DENTRO do `<dd>`: um `<p>` solto ao lado do `<dd>` reprova
 * no axe (`definition-list`).
 */
import { CollapsibleSummary, SummaryInline } from '@/components/shared/collapsible-summary';
import { Card } from '@/components/ui/card';
import type { MappingListResponse, MappingSituation } from '@/lib/contracts';
import { cn } from '@/lib/utils';

export type MappingSituationCounts = MappingListResponse['counts'];

/** As cinco chaves clicáveis: o universo e as quatro situações. */
export type MappingCountKey = 'total' | MappingSituation;

/** A tabela do mapeamento (fonte única): contador → `situation` da URL. */
export function mappingCountFilterSituation(key: MappingCountKey): MappingSituation | null {
  return key === 'total' ? null : key;
}

/** Ativo quando a situação da URL bate exatamente com a do contador. */
export function isMappingCountActive(
  current: MappingSituation | null,
  key: MappingCountKey,
): boolean {
  return current === mappingCountFilterSituation(key);
}

interface CountStat {
  key: MappingCountKey;
  label: string;
  value: number;
  /** Frase curta; some no mobile para caber em 390px. */
  hint: string;
}

function stats(counts: MappingSituationCounts): CountStat[] {
  return [
    { key: 'total', label: 'Categorias', value: counts.total, hint: 'Todo o universo do destino' },
    {
      key: 'herdada',
      label: 'Herdadas da origem',
      value: counts.herdada,
      hint: 'Propostas pelas categorias do Omie',
    },
    {
      key: 'confirmada',
      label: 'Confirmadas',
      value: counts.confirmada,
      hint: 'Assumidas por alguém da equipe',
    },
    {
      key: 'nao_mapear',
      label: 'Não mapear',
      value: counts.naoMapear,
      // "Não mapear" é decisão, não pendência: a frase diz isso.
      hint: 'Decidido: fica fora do destino',
    },
    {
      key: 'sem_decisao',
      label: 'Sem decisão',
      value: counts.semDecisao,
      hint: 'Aguardando alguém decidir',
    },
  ];
}

function countLabel(count: number): string {
  return `${count} ${count === 1 ? 'categoria' : 'categorias'}`;
}

export function MappingSituationCountsBlock({
  counts,
  active,
  onSelect,
}: {
  counts: MappingSituationCounts;
  /** A situação atual da URL: decide qual botão está `aria-pressed`. */
  active: MappingSituation | null;
  /** Clique num valor: quem chama aplica (ou desfaz) o recorte na URL. */
  onSelect: (key: MappingCountKey) => void;
}) {
  const items = stats(counts);
  return (
    <CollapsibleSummary
      storageKey="de-para"
      collapsed={
        <SummaryInline
          items={items.map((stat) => ({
            key: stat.key,
            value: stat.value,
            label: stat.label.toLowerCase(),
          }))}
        />
      }
      footnote="Contagens do destino inteiro, na competência corrente. Clique num valor para filtrar a lista."
    >
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        {items.map((stat) => {
          const isActive = isMappingCountActive(active, stat.key);
          return (
            <Card key={stat.key} variant="elevated" className="p-1.5">
              <dt className="text-muted-foreground px-2 pt-1 text-xs font-medium">{stat.label}</dt>
              <dd>
                {/* Ativo = `aria-pressed` + anel + fundo `accent`, com TODO o texto
                    em `accent-foreground` (par travado no `theme-contrast.test.ts`).
                    Hover muda só o anel, para não criar par de cor sem teste. */}
                <button
                  type="button"
                  aria-pressed={isActive}
                  aria-label={`${stat.label}: ${countLabel(stat.value)}. Filtrar a lista`}
                  onClick={() => onSelect(stat.key)}
                  className={cn(
                    'focus-visible:ring-ring hover:ring-ring block w-full cursor-pointer rounded-md px-2 py-1 text-left transition-colors hover:ring-1 focus-visible:outline-none focus-visible:ring-2',
                    isActive && 'bg-accent text-accent-foreground ring-ring ring-2',
                  )}
                >
                  <span className="block text-xl font-semibold tabular-nums">{stat.value}</span>
                  <span
                    className={cn('hidden text-xs sm:block', !isActive && 'text-muted-foreground')}
                  >
                    {stat.hint}
                  </span>
                </button>
              </dd>
            </Card>
          );
        })}
      </dl>
    </CollapsibleSummary>
  );
}

export function MappingSituationCountsSkeleton() {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Carregando as contagens do de-para"
      className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5"
    >
      {Array.from({ length: 5 }).map((_, index) => (
        <div key={index} className="space-y-2 rounded-lg border p-3">
          <div className="bg-muted h-3 w-20 animate-pulse rounded" />
          <div className="bg-muted h-6 w-12 animate-pulse rounded" />
        </div>
      ))}
    </div>
  );
}
