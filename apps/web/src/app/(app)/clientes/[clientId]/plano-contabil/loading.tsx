/**
 * Skeleton da rota "Plano contábil" do cliente.
 *
 * Proporcional ao layout real (cabeçalho com a ação + barra de filtros +
 * tabela de 5 colunas + paginação + seção da conta do banco). Reaproveitado
 * como `fallback` do `<Suspense>` da página.
 */
export default function AccountingChartLoading() {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Carregando o plano contábil do cliente"
      className="flex flex-col gap-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-2">
          <div className="bg-muted h-5 w-40 animate-pulse rounded" />
          <div className="bg-muted h-3 w-80 max-w-full animate-pulse rounded" />
        </div>
        <div className="bg-muted h-10 w-44 animate-pulse rounded-md" />
      </div>

      <div className="flex flex-col gap-3 lg:flex-row">
        <div className="bg-muted h-10 animate-pulse rounded-md lg:w-72" />
        <div className="bg-muted h-10 animate-pulse rounded-md lg:w-64" />
        <div className="bg-muted h-10 animate-pulse rounded-md lg:w-56" />
      </div>

      <div className="space-y-px rounded-lg border p-4">
        {Array.from({ length: 6 }).map((_, row) => (
          <div key={row} className="flex items-center gap-4 py-3">
            {Array.from({ length: 5 }).map((__, cell) => (
              <div key={cell} className="bg-muted h-4 flex-1 animate-pulse rounded" />
            ))}
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between border-t px-1 py-3">
        <div className="bg-muted h-4 w-24 animate-pulse rounded" />
        <div className="bg-muted h-9 w-56 animate-pulse rounded-md" />
      </div>

      <div className="space-y-2">
        <div className="bg-muted h-5 w-32 animate-pulse rounded" />
        <div className="bg-muted h-24 animate-pulse rounded-lg" />
      </div>
    </div>
  );
}
