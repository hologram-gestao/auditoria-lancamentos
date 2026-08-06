/**
 * Skeleton da rota "Glossário" do cliente.
 *
 * Proporcional ao layout real (cabeçalho + tabela de 4 colunas + rodapé de
 * paginação) — um spinner genérico faria a tela pular de tamanho quando os
 * dados chegassem. Reaproveitado como `fallback` do `<Suspense>` da própria
 * página, para os dois caminhos mostrarem a mesma coisa.
 */
export default function GlossaryLoading() {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Carregando glossário do cliente"
      className="flex h-full flex-col gap-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-2">
          <div className="bg-muted h-5 w-32 animate-pulse rounded" />
          <div className="bg-muted h-3 w-80 animate-pulse rounded" />
        </div>
        <div className="bg-muted h-10 w-36 animate-pulse rounded-md" />
      </div>

      <div className="space-y-px rounded-lg border p-4">
        {Array.from({ length: 5 }).map((_, row) => (
          <div key={row} className="flex items-center gap-4 py-3">
            {Array.from({ length: 4 }).map((__, cell) => (
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
