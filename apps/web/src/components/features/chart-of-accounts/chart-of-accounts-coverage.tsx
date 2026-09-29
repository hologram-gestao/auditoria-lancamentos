/**
 * Bloco de cobertura do plano de contas (Sprint 10 / R3; cards que filtram
 * desde a 86e3f55bc).
 *
 * "50 categorias · 46 ativas · 33 com destino · 13 sem destino declarado · 5
 * com conta contábil" — quem vai construir o de-para precisa ver o tamanho do
 * buraco ANTES de começar.
 *
 * ⚠️ **As cinco contagens vêm do SERVIDOR**, da rota `/coverage`, calculadas
 * sobre o conjunto inteiro do cliente. Somar a página no navegador daria um
 * número que muda conforme a paginação e o filtro — e que estaria errado em
 * toda tela com mais de uma página. Este componente é só exibição: ele não faz
 * conta nenhuma, nem a de `comDestino + semDestino`.
 *
 * **Cada valor é um botão que FILTRA a lista** (86e3f55bc), no mesmo desenho
 * dos totais da carteira (`client-titles-summary.tsx`): o recorte vai para a
 * URL (`status`, `hasDreCode`, `hasAccountingCode`), aplicado no servidor. A
 * tabela de `coverageFilterParams` é a fonte única do mapeamento e decide
 * também quando um botão está ATIVO (os três parâmetros batem exatamente).
 * "Com destino", "Sem destino declarado" e "Com conta contábil" levam
 * `status=ativa` de propósito: a cobertura conta essas três só sobre as
 * ATIVAS, e sem a situação o total da paginação passaria do número do card
 * sempre que uma categoria inativa tivesse destino (na amostra real, 37 contra
 * 33). Decisão do Pedro em 28/09/2026.
 *
 * `<dl>` e não uma tabela: são cinco pares rótulo/valor, não linhas de um
 * conjunto — e o leitor de tela anuncia o par junto. O botão vive DENTRO do
 * `<dd>`; um `<p>` solto ao lado do `<dd>` reprova no axe (`definition-list`).
 */
import { CollapsibleSummary, SummaryInline } from '@/components/shared/collapsible-summary';
import type { ChartOfAccountsCoverage, ChartOfAccountsStatus } from '@/lib/contracts';
import { formatCreatedAt } from '@/lib/format';
import { cn } from '@/lib/utils';

/** As cinco chaves clicáveis, uma por card. */
export type CoverageFilterKey =
  | 'total'
  | 'ativas'
  | 'comDestino'
  | 'semDestino'
  | 'comContaContabil';

/** O recorte da lista que o clique aplica — e que decide se o botão está ativo. */
export interface CoverageFilterParams {
  status: ChartOfAccountsStatus | null;
  hasDreCode: boolean | null;
  hasAccountingCode: boolean | null;
}

/**
 * A tabela do mapeamento (fonte única): card → parâmetros da URL.
 *
 *   Categorias            → sem status, sem hasDreCode, sem hasAccountingCode
 *   Ativas                → status=ativa
 *   Com destino           → status=ativa, hasDreCode=true
 *   Sem destino declarado → status=ativa, hasDreCode=false
 *   Com conta contábil    → status=ativa, hasAccountingCode=true
 */
export function coverageFilterParams(key: CoverageFilterKey): CoverageFilterParams {
  switch (key) {
    case 'total':
      return { status: null, hasDreCode: null, hasAccountingCode: null };
    case 'ativas':
      return { status: 'ativa', hasDreCode: null, hasAccountingCode: null };
    case 'comDestino':
      return { status: 'ativa', hasDreCode: true, hasAccountingCode: null };
    case 'semDestino':
      return { status: 'ativa', hasDreCode: false, hasAccountingCode: null };
    case 'comContaContabil':
      return { status: 'ativa', hasDreCode: null, hasAccountingCode: true };
  }
}

/** Ativo quando os TRÊS parâmetros da URL batem exatamente com a linha da tabela. */
export function isCoverageFilterActive(
  current: CoverageFilterParams,
  key: CoverageFilterKey,
): boolean {
  const expected = coverageFilterParams(key);
  return (
    current.status === expected.status &&
    current.hasDreCode === expected.hasDreCode &&
    current.hasAccountingCode === expected.hasAccountingCode
  );
}

interface CoverageStat {
  key: CoverageFilterKey;
  label: string;
  value: number;
  /** Frase curta do que o número significa; some no mobile para caber em 390px. */
  hint: string;
}

function stats(coverage: ChartOfAccountsCoverage): CoverageStat[] {
  return [
    { key: 'total', label: 'Categorias', value: coverage.total, hint: 'Tudo que veio da origem' },
    { key: 'ativas', label: 'Ativas', value: coverage.ativas, hint: 'Em uso no cadastro' },
    {
      key: 'comDestino',
      label: 'Com destino',
      value: coverage.comDestino,
      hint: 'Já trazem conta de demonstrativo',
    },
    {
      key: 'semDestino',
      label: 'Sem destino declarado',
      value: coverage.semDestino,
      // Não é pendência: transferências e totalizadoras não têm destino próprio.
      hint: 'Informação, não pendência',
    },
    {
      key: 'comContaContabil',
      label: 'Com conta contábil',
      value: coverage.comContaContabil,
      hint: 'Vinculadas a uma conta contábil',
    },
  ];
}

function countLabel(count: number): string {
  return `${count} ${count === 1 ? 'categoria' : 'categorias'}`;
}

export function ChartOfAccountsCoverageBlock({
  coverage,
  active,
  onSelect,
}: {
  coverage: ChartOfAccountsCoverage;
  /** O recorte atual da URL: decide qual botão está `aria-pressed`. */
  active: CoverageFilterParams;
  /** Clique num valor: quem chama aplica (ou desfaz) o recorte na URL. */
  onSelect: (key: CoverageFilterKey) => void;
}) {
  const items = stats(coverage);
  return (
    <CollapsibleSummary
      storageKey="plano-de-contas"
      collapsed={
        <SummaryInline
          items={items.map((stat) => ({
            key: stat.key,
            value: stat.value,
            label: stat.label.toLowerCase(),
          }))}
        />
      }
      footnote={
        <>
          Contagens do plano de contas inteiro
          {coverage.syncedAt != null
            ? `, com referência em ${formatCreatedAt(coverage.syncedAt)}`
            : ''}
          . Clique num valor para filtrar a lista.
        </>
      }
    >
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        {items.map((stat) => {
          const isActive = isCoverageFilterActive(active, stat.key);
          return (
            // A `<div>` entre o `<dl>` e o par é permitida; um `<p>` solto ao lado
            // do `<dd>` NÃO é (axe `definition-list`, SERIOUS) — por isso a frase
            // de apoio vive DENTRO do `<dd>`, junto do número que ela explica.
            <div key={stat.key} className="bg-card rounded-lg border p-1.5">
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
            </div>
          );
        })}
      </dl>
    </CollapsibleSummary>
  );
}

export function ChartOfAccountsCoverageSkeleton() {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Carregando a cobertura do plano de contas"
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
