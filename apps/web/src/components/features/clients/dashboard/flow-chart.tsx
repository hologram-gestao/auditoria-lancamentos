/**
 * Gráfico do fluxo previsto pelos vencimentos (86e3k1q54), em SVG inline e sem
 * biblioteca de gráfico.
 *
 * - Barras agrupadas por faixa (`ate_7` … `90_mais`): a receber em `info`, a pagar
 *   em `warning`. **Vencidos ficam FORA das barras**: o acumulado vencido
 *   esmagaria as faixas futuras, e ele entra numa linha de texto à parte.
 * - Escala linear a partir do máximo da série, com três linhas de grade e
 *   rótulos "R$ N mil" ("R$ N mi" a partir de um milhão). O único cálculo aqui
 *   é a GEOMETRIA (altura da barra); todo número exibido veio do servidor.
 * - O SVG estica na largura (`preserveAspectRatio="none"`) e só desenha
 *   retângulos e linhas; todo TEXTO é HTML posicionado por porcentagem, para não
 *   encolher a 5px em 390px nem se deformar com o esticamento.
 * - Cor por token: `style="fill: hsl(var(--info))"`, nunca hex.
 * - Acessibilidade: `role="img"` com um resumo da série no `aria-label`, e uma
 *   tabela `sr-only` com faixa, a receber, a pagar e líquido. O desenho e os
 *   rótulos visuais ficam fora da árvore de acessibilidade, para o leitor de tela
 *   não ler a mesma coisa duas vezes.
 */
import { Money } from '@/components/shared/money';
import type { TitlesFlowBucket, TitlesFlowBucketCode } from '@/lib/contracts';
import { formatBRL } from '@/lib/format';

/** As faixas que viram barra, na ordem do eixo. `vencidos` fica de fora. */
export const CHART_BUCKETS = [
  'ate_7',
  '8_30',
  '31_60',
  '61_90',
  '90_mais',
] as const satisfies readonly Exclude<TitlesFlowBucketCode, 'vencidos'>[];

export const FLOW_BUCKET_LABELS: Record<TitlesFlowBucketCode, string> = {
  vencidos: 'Vencidos',
  ate_7: 'Até 7 dias',
  '8_30': '8 a 30 dias',
  '31_60': '31 a 60 dias',
  '61_90': '61 a 90 dias',
  '90_mais': '90+ dias',
};

const GRID_LINES = 3;
const VIEW_WIDTH = 500;
const VIEW_HEIGHT = 160;
/** Espaço livre no topo do desenho, para a barra mais alta não encostar na borda. */
const TOP_PADDING = 18;

/** Arredonda para cima até um passo "redondo" (1, 2, 2,5 ou 5 × 10^k). */
export function niceStep(raw: number): number {
  if (raw <= 0) return 0;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  for (const factor of [1, 2, 2.5, 5, 10]) {
    if (raw <= factor * magnitude) return factor * magnitude;
  }
  return 10 * magnitude;
}

const AXIS_FORMATTER = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 });

/**
 * Rótulo do eixo: "R$ 1,5 mi" a partir de um milhão (nunca "R$ 1.500 mil"),
 * "R$ 500 mil" a partir de mil e "R$ 800" abaixo disso.
 */
export function axisLabel(value: number): string {
  if (value >= 1_000_000) return `R$ ${AXIS_FORMATTER.format(value / 1_000_000)} mi`;
  if (value >= 1000) return `R$ ${AXIS_FORMATTER.format(value / 1000)} mil`;
  return `R$ ${AXIS_FORMATTER.format(value)}`;
}

/** Os valores das linhas de grade (de baixo para cima), a partir do máximo da série. */
export function gridValues(max: number): number[] {
  const step = niceStep(max / GRID_LINES);
  if (step === 0) return [];
  return Array.from({ length: GRID_LINES }, (_, i) => step * (i + 1));
}

function amount(value: string): number {
  const num = Number(value);
  return Number.isFinite(num) ? num : 0;
}

function bucketLabel(code: TitlesFlowBucketCode): string {
  return FLOW_BUCKET_LABELS[code];
}

export function FlowChart({ buckets }: { buckets: readonly TitlesFlowBucket[] }) {
  const byCode = new Map(buckets.map((b) => [b.bucket, b]));
  const series = CHART_BUCKETS.map((code) => byCode.get(code)).filter(
    (b): b is TitlesFlowBucket => b !== undefined,
  );
  const max = Math.max(
    0,
    ...series.flatMap((b) => [amount(b.aReceber.total), amount(b.aPagar.total)]),
  );
  const grid = gridValues(max);
  const top = grid.at(-1) ?? 0;
  const plotHeight = VIEW_HEIGHT - TOP_PADDING;
  const groupWidth = VIEW_WIDTH / CHART_BUCKETS.length;
  const barWidth = groupWidth * 0.28;
  const gap = groupWidth * 0.04;

  const heightOf = (value: string) => (top === 0 ? 0 : (amount(value) / top) * plotHeight);
  const yOf = (value: number) => VIEW_HEIGHT - (top === 0 ? 0 : (value / top) * plotHeight);

  const summary = series
    .map(
      (b) =>
        `${bucketLabel(b.bucket)}: ${formatBRL(b.aReceber.total)} a receber e ${formatBRL(b.aPagar.total)} a pagar`,
    )
    .join('; ');

  return (
    <div className="flex flex-col gap-2">
      {/* O eixo tem CALHA própria à esquerda (`pl-14`): rótulo de grade nunca
          cobre o topo de uma barra. A linha de faixas abaixo usa a mesma calha
          para as colunas alinharem com os grupos. */}
      <div className="relative h-44 pl-14">
        <div aria-hidden="true" className="pointer-events-none absolute inset-y-0 left-0 w-14">
          {grid.map((value) => (
            <span
              key={value}
              data-testid="flow-chart-axis-label"
              className="text-muted-foreground absolute left-0 -translate-y-1/2 whitespace-nowrap text-[11px] leading-none"
              style={{ top: `${(yOf(value) / VIEW_HEIGHT) * 100}%` }}
            >
              {axisLabel(value)}
            </span>
          ))}
        </div>
        <div className="relative h-full">
          <svg
            role="img"
            aria-label={`Fluxo previsto por faixa de vencimento. ${summary}.`}
            viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
            preserveAspectRatio="none"
            className="absolute inset-0 h-full w-full"
            data-testid="flow-chart-svg"
          >
            {grid.map((value) => (
              <line
                key={value}
                data-testid="flow-chart-grid"
                x1={0}
                x2={VIEW_WIDTH}
                y1={yOf(value)}
                y2={yOf(value)}
                vectorEffect="non-scaling-stroke"
                style={{ stroke: 'hsl(var(--border))' }}
                strokeDasharray="4 4"
              />
            ))}
            <line
              x1={0}
              x2={VIEW_WIDTH}
              y1={VIEW_HEIGHT}
              y2={VIEW_HEIGHT}
              vectorEffect="non-scaling-stroke"
              style={{ stroke: 'hsl(var(--border))' }}
            />
            {series.map((b, index) => {
              const center = groupWidth * index + groupWidth / 2;
              const receberHeight = heightOf(b.aReceber.total);
              const pagarHeight = heightOf(b.aPagar.total);
              return (
                <g key={b.bucket} data-testid="flow-chart-group" data-bucket={b.bucket}>
                  <rect
                    x={center - gap / 2 - barWidth}
                    y={VIEW_HEIGHT - receberHeight}
                    width={barWidth}
                    height={receberHeight}
                    style={{ fill: 'hsl(var(--info))' }}
                  />
                  <rect
                    x={center + gap / 2}
                    y={VIEW_HEIGHT - pagarHeight}
                    width={barWidth}
                    height={pagarHeight}
                    style={{ fill: 'hsl(var(--warning))' }}
                  />
                </g>
              );
            })}
          </svg>
          {/* Faixa sem título nenhum: o texto no lugar das barras. */}
          <div aria-hidden="true" className="pointer-events-none absolute inset-0 grid grid-cols-5">
            {series.map((b) => (
              <div key={b.bucket} className="flex items-end justify-center pb-1">
                {b.aReceber.count === 0 && b.aPagar.count === 0 && (
                  <span className="text-muted-foreground text-center text-xs">sem títulos</span>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
      {/* Faixa e líquido. Em `sm`+ uma coluna por grupo, sob as barras; abaixo
          de `sm` cinco valores com sinal não cabem lado a lado (um encostava no
          outro em 390px), então o líquido vira lista e as colunas levam só o nome. */}
      <div aria-hidden="true" className="grid grid-cols-5 gap-1 pl-14 text-center">
        {series.map((b) => (
          <div key={b.bucket} className="flex min-w-0 flex-col items-center gap-0.5">
            <span className="text-muted-foreground text-xs">{bucketLabel(b.bucket)}</span>
            <Money value={b.net} tone="sign" className="hidden text-xs font-medium sm:inline" />
          </div>
        ))}
      </div>
      <dl aria-hidden="true" className="space-y-1 text-xs sm:hidden">
        {series.map((b) => (
          <div key={b.bucket} className="flex justify-between gap-2">
            <dt className="text-muted-foreground">Líquido {bucketLabel(b.bucket).toLowerCase()}</dt>
            <dd className="font-medium">
              <Money value={b.net} tone="sign" />
            </dd>
          </div>
        ))}
      </dl>
      {/* O `sr-only` vai num `div`, não na tabela: tabela ignora o `width: 1px`
          (cresce até o conteúdo) e empurrava a página para 422px em 390px. */}
      <div className="sr-only">
        <table>
          <caption>Fluxo previsto por faixa de vencimento</caption>
          <thead>
            <tr>
              <th scope="col">Faixa</th>
              <th scope="col">A receber</th>
              <th scope="col">A pagar</th>
              <th scope="col">Líquido</th>
            </tr>
          </thead>
          <tbody>
            {series.map((b) => (
              <tr key={b.bucket}>
                <th scope="row">{bucketLabel(b.bucket)}</th>
                <td>{formatBRL(b.aReceber.total)}</td>
                <td>{formatBRL(b.aPagar.total)}</td>
                <td>
                  <Money value={b.net} tone="sign" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
