/**
 * Skeleton da rota "Plano de Contas" do cliente.
 *
 * Proporcional ao layout real (cabeçalho + bloco de cobertura de 5 cartões +
 * barra de filtros + tabela de 5 colunas + rodapé de paginação) — um spinner
 * genérico faria a tela pular de tamanho quando os dados chegassem. Reaproveitado
 * como `fallback` do `<Suspense>` da própria página, para os dois caminhos
 * mostrarem a mesma coisa.
 */
export default function ChartOfAccountsLoading() {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Carregando o plano de contas do cliente"
      className="flex h-full flex-col gap-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-2">
          <div className="bg-muted h-5 w-40 animate-pulse rounded" />
          <div className="bg-muted h-3 w-80 animate-pulse rounded" />
        </div>
        <div className="bg-muted h-10 w-44 animate-pulse rounded-md" />
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {Array.from({ length: 5 }).map((_, index) => (
          <div key={index} className="space-y-2 rounded-lg border p-3">
            <div className="bg-muted h-3 w-20 animate-pulse rounded" />
            <div className="bg-muted h-7 w-12 animate-pulse rounded" />
          </div>
        ))}
      </div>

      <div className="flex flex-col gap-3 lg:flex-row">
        <div className="bg-muted h-10 flex-1 animate-pulse rounded-md" />
        <div className="bg-muted h-10 animate-pulse rounded-md lg:w-56" />
        <div className="bg-muted h-10 animate-pulse rounded-md lg:w-56" />
      </div>

      <div className="space-y-px rounded-lg border p-4">
        {Array.from({ length: 5 }).map((_, row) => (
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
    </div>
  );
}
