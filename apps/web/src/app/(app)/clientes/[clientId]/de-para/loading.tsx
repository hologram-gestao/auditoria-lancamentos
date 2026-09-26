/**
 * Skeleton da rota "De-para" do cliente.
 *
 * Proporcional ao layout real (cabeçalho com ações + seletor de destino + abas +
 * barra de filtros + tabela de 5 colunas + rodapé de paginação) — um spinner
 * genérico faria a tela pular de tamanho quando os dados chegassem.
 * Reaproveitado como `fallback` do `<Suspense>` da própria página.
 */
export default function ClientMappingLoading() {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Carregando o de-para do cliente"
      className="flex h-full flex-col gap-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-2">
          <div className="bg-muted h-5 w-24 animate-pulse rounded" />
          <div className="bg-muted h-3 w-80 max-w-full animate-pulse rounded" />
        </div>
        <div className="flex gap-2">
          <div className="bg-muted h-10 w-28 animate-pulse rounded-md" />
          <div className="bg-muted h-10 w-28 animate-pulse rounded-md" />
        </div>
      </div>

      <div className="bg-muted h-10 w-full animate-pulse rounded-md sm:w-80" />
      <div className="bg-muted h-10 w-72 max-w-full animate-pulse rounded-md" />

      <div className="flex flex-col gap-3 lg:flex-row">
        <div className="bg-muted h-10 flex-1 animate-pulse rounded-md" />
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
