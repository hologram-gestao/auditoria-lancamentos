/**
 * Bloco de cobertura do plano de contas (Sprint 10 / R3).
 *
 * "50 categorias · 50 ativas · 37 com destino · 13 sem destino declarado · 6
 * com conta contábil" — quem vai construir o de-para precisa ver o tamanho do
 * buraco ANTES de começar.
 *
 * ⚠️ **As cinco contagens vêm do SERVIDOR**, da rota `/coverage`, calculadas
 * sobre o conjunto inteiro do cliente. Somar a página no navegador daria um
 * número que muda conforme a paginação e o filtro — e que estaria errado em
 * toda tela com mais de uma página. Este componente é só exibição: ele não faz
 * conta nenhuma, nem a de `comDestino + semDestino`.
 *
 * `<dl>` e não uma tabela: são cinco pares rótulo/valor, não linhas de um
 * conjunto — e o leitor de tela anuncia o par junto.
 */
import type { ChartOfAccountsCoverage } from '@/lib/contracts';

interface CoverageStat {
  label: string;
  value: number;
  /** Frase curta do que o número significa; some no mobile para caber em 390px. */
  hint: string;
}

function stats(coverage: ChartOfAccountsCoverage): CoverageStat[] {
  return [
    { label: 'Categorias', value: coverage.total, hint: 'Tudo que veio da origem' },
    { label: 'Ativas', value: coverage.ativas, hint: 'Em uso no cadastro' },
    {
      label: 'Com destino',
      value: coverage.comDestino,
      hint: 'Já trazem conta de demonstrativo',
    },
    {
      label: 'Sem destino declarado',
      value: coverage.semDestino,
      // Não é pendência: transferências e totalizadoras não têm destino próprio.
      hint: 'Informação, não pendência',
    },
    {
      label: 'Com conta contábil',
      value: coverage.comContaContabil,
      hint: 'Vinculadas a uma conta contábil',
    },
  ];
}

export function ChartOfAccountsCoverageBlock({ coverage }: { coverage: ChartOfAccountsCoverage }) {
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {stats(coverage).map((stat) => (
        // A `<div>` entre o `<dl>` e o par é permitida; um `<p>` solto ao lado
        // do `<dd>` NÃO é (axe `definition-list`, SERIOUS) — por isso a frase
        // de apoio vive DENTRO do `<dd>`, junto do número que ela explica.
        <div key={stat.label} className="bg-card space-y-1 rounded-lg border p-3">
          <dt className="text-muted-foreground text-xs font-medium">{stat.label}</dt>
          <dd>
            <span className="block text-2xl font-semibold tabular-nums">{stat.value}</span>
            <span className="text-muted-foreground hidden text-xs sm:block">{stat.hint}</span>
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function ChartOfAccountsCoverageSkeleton() {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Carregando a cobertura do plano de contas"
      className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5"
    >
      {Array.from({ length: 5 }).map((_, index) => (
        <div key={index} className="space-y-2 rounded-lg border p-3">
          <div className="bg-muted h-3 w-20 animate-pulse rounded" />
          <div className="bg-muted h-7 w-12 animate-pulse rounded" />
        </div>
      ))}
    </div>
  );
}
