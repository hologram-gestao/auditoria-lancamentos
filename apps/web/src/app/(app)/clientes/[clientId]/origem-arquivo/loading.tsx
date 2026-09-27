/**
 * Skeleton da rota "Origem por arquivo" do cliente.
 *
 * Proporcional ao layout real (cabeçalho + card do mapeamento + card do envio
 * com três campos + tabela de arquivos processados com 4 colunas) — um spinner
 * genérico faria a tela pular de tamanho quando os dados chegassem.
 * Reaproveitado como `fallback` do `<Suspense>` da própria página.
 */
export default function FileOriginLoading() {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Carregando a origem por arquivo do cliente"
      className="flex flex-col gap-4"
    >
      <div className="space-y-2">
        <div className="bg-muted h-5 w-40 animate-pulse rounded" />
        <div className="bg-muted h-3 w-80 max-w-full animate-pulse rounded" />
      </div>

      <div className="bg-card space-y-3 rounded-lg border p-4">
        <div className="bg-muted h-4 w-48 animate-pulse rounded" />
        <div className="grid gap-2 sm:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="bg-muted h-4 animate-pulse rounded" />
          ))}
        </div>
      </div>

      <div className="bg-card space-y-3 rounded-lg border p-4">
        <div className="bg-muted h-4 w-32 animate-pulse rounded" />
        <div className="grid gap-3 sm:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="bg-muted h-10 animate-pulse rounded-md" />
          ))}
        </div>
        <div className="bg-muted h-10 w-32 animate-pulse rounded-md" />
      </div>

      <div className="space-y-px rounded-lg border p-4">
        {Array.from({ length: 3 }).map((_, row) => (
          <div key={row} className="flex items-center gap-4 py-3">
            {Array.from({ length: 4 }).map((__, cell) => (
              <div key={cell} className="bg-muted h-4 flex-1 animate-pulse rounded" />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
