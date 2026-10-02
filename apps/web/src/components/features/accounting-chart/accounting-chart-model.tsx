/**
 * O MODELO de planilha do plano contábil, documentado na própria tela
 * (Sprint 16 — FRONT 16.5 / R1).
 *
 * Transcrito de `apps/api/docs/plano-contabil-modelo-de-planilha.md` e da
 * descrição da rota de importação no OpenAPI — é o modelo PRÓPRIO da plataforma
 * (S-1 assumida: o escritório exporta o plano do sistema contábil numa
 * planilha e a ajusta a estas colunas). O plano exportado do Domínio em .xlsx
 * também é aceito sem ajuste (86e3gkd7y): o backend reconhece o layout e o
 * converte para este modelo, e a última linha da seção diz isso. Se o leitor do
 * backend mudar, a doc muda e este componente muda junto.
 *
 * Server component: só renderiza texto.
 */

const COLUMNS: ReadonlyArray<{ name: string; required: boolean; rule: string }> = [
  {
    name: 'codigo_reduzido',
    required: true,
    rule: 'O código que vai no arquivo contábil. Letras, dígitos, "." e "-", até 20 caracteres, sem repetir.',
  },
  { name: 'nome', required: true, rule: 'Nome da conta, até 200 caracteres.' },
  {
    name: 'tipo',
    required: true,
    rule: '"analitica" (recebe lançamento) ou "sintetica" (só agrupa).',
  },
  {
    name: 'classificacao',
    required: false,
    rule: 'A classificação hierárquica (ex.: 1.1.1.02.001), até 40 caracteres.',
  },
];

const EXAMPLE = [
  'codigo_reduzido;nome;tipo;classificacao',
  '10;Ativo circulante;sintetica;1.1',
  '649;Banco conta movimento;analitica;1.1.1.02.001',
  '662;Aluguéis a receber;analitica;1.1.2.01.004',
].join('\n');

export function AccountingChartModel({ headingId }: { headingId: string }) {
  return (
    <section aria-labelledby={headingId} className="space-y-3 text-sm">
      <h3 id={headingId} className="font-medium">
        Modelo da planilha
      </h3>
      <p className="text-muted-foreground">
        CSV em UTF-8 separado por <code className="font-mono">;</code> ou XLSX (só a primeira aba).
        Cabeçalho na linha 1, colunas em qualquer ordem e nenhuma coluna além destas:
      </p>
      <dl className="space-y-2">
        {COLUMNS.map((column) => (
          <div key={column.name} className="space-y-0.5">
            <dt className="flex flex-wrap items-center gap-2">
              <code className="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">
                {column.name}
              </code>
              <span className="text-muted-foreground text-xs">
                {column.required ? 'obrigatória' : 'opcional'}
              </span>
            </dt>
            <dd className="text-muted-foreground">{column.rule}</dd>
          </div>
        ))}
      </dl>
      <div className="space-y-1">
        <p className="text-muted-foreground text-xs">Exemplo (CSV):</p>
        {/* Quebra a linha em vez de rolar: em 390px o exemplo cabe no card sem
            virar uma região rolável a mais. */}
        <pre className="bg-muted whitespace-pre-wrap break-all rounded-md p-3 font-mono text-xs">
          {EXAMPLE}
        </pre>
      </div>
      <p className="text-muted-foreground">
        O plano de contas exportado do Domínio em .xlsx também é aceito, do jeito que sai do
        sistema: a plataforma reconhece o arquivo e o converte para este modelo.
      </p>
    </section>
  );
}
