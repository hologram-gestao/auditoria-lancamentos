/**
 * Vinhetas dos estados vazios (86e3h57b5), no espírito da `SecurityVignette` da
 * landing: SVG puro e decorativo (`aria-hidden`), uma por estado, para o vazio dizer
 * de relance do que ele é.
 *
 * Regras, travadas em `__tests__/vignettes.test.tsx`:
 *   - no máximo 20 elementos SVG cada;
 *   - cor SÓ por token, pelas classes `fill-*`/`stroke-*` do Tailwind: `card` e
 *     `border` na moldura, `muted-foreground` no traço neutro e `primary` no que é da
 *     ação (o verde no Hologram, navy e índigo no claro e no escuro; nunca `--brand`);
 *   - cada parte (`data-vig-part`, com o índice em `--vig-i`) entra em 600 ms, uma
 *     vez, e PARA: a classe `vignette` do `globals.css`, só sob
 *     `prefers-reduced-motion: no-preference`. Sem loop.
 *
 * O tamanho vem de quem desenha a moldura (`EmptyState`, pela altura); aqui só o
 * `viewBox`, igual em todas (120 × 80), para as cinco terem o mesmo peso.
 */
import { cn } from '@/lib/utils';

const LINE = 'stroke-muted-foreground/50';
const FRAME = 'fill-card stroke-border';
const ACTION = 'stroke-primary';

function part(index: number): React.SVGProps<SVGGElement> {
  return {
    'data-vig-part': '',
    style: { '--vig-i': index } as React.CSSProperties,
  } as React.SVGProps<SVGGElement>;
}

function Vignette({
  name,
  className,
  children,
}: {
  name: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      data-vignette={name}
      viewBox="0 0 120 80"
      fill="none"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn('vignette h-20 w-auto', className)}
    >
      {children}
    </svg>
  );
}

/** Sem conciliações: dois extratos lado a lado, ainda não cruzados. */
export function ReconciliationsVignette({ className }: { className?: string }) {
  return (
    <Vignette name="reconciliations" className={className}>
      <g {...part(0)}>
        <rect className={FRAME} x={10} y={14} width={38} height={52} rx={4} strokeWidth={1.5} />
        <path className={LINE} d="M18 28h22M18 38h22M18 48h14" strokeWidth={2} />
      </g>
      <g {...part(1)}>
        <rect className={FRAME} x={72} y={14} width={38} height={52} rx={4} strokeWidth={1.5} />
        <path className={LINE} d="M80 28h22M80 38h22M80 48h14" strokeWidth={2} />
      </g>
      <g {...part(2)}>
        <path className={ACTION} d="M52 40h16" strokeWidth={2} strokeDasharray="3 3" />
        <circle className={cn(ACTION, 'fill-card')} cx={60} cy={40} r={7} strokeWidth={1.5} />
        <path className={ACTION} d="M57 40l2 2 4-4" strokeWidth={1.8} />
      </g>
    </Vignette>
  );
}

/** Sem origem conectada: o sistema de um lado, o plugue do outro, o fio solto. */
export function OriginVignette({ className }: { className?: string }) {
  return (
    <Vignette name="origin" className={className}>
      <g {...part(0)}>
        <path className={FRAME} d="M12 22v32c0 3.3 8 6 18 6s18-2.7 18-6V22" strokeWidth={1.5} />
        <ellipse className={FRAME} cx={30} cy={22} rx={18} ry={6} strokeWidth={1.5} />
        <path className={LINE} d="M12 38c0 3.3 8 6 18 6s18-2.7 18-6" strokeWidth={1.5} />
      </g>
      <g {...part(1)}>
        <path className={ACTION} d="M52 40h14" strokeWidth={2} strokeDasharray="3 3" />
      </g>
      <g {...part(2)}>
        <path className={ACTION} d="M70 40h4" strokeWidth={2} />
        <rect
          className={cn(ACTION, 'fill-card')}
          x={74}
          y={30}
          width={22}
          height={20}
          rx={4}
          strokeWidth={1.5}
        />
        <path className={ACTION} d="M96 35h10M96 45h10" strokeWidth={2} />
      </g>
    </Vignette>
  );
}

/** Carteira nunca sincronizada: os títulos empilhados esperando a sincronização. */
export function PortfolioVignette({ className }: { className?: string }) {
  return (
    <Vignette name="portfolio" className={className}>
      <g {...part(0)}>
        <rect className={FRAME} x={20} y={12} width={52} height={38} rx={4} strokeWidth={1.5} />
        <rect className={FRAME} x={12} y={22} width={52} height={38} rx={4} strokeWidth={1.5} />
        <path className={LINE} d="M20 34h28M20 43h20M20 52h24" strokeWidth={2} />
      </g>
      <g {...part(1)}>
        <circle className={cn(ACTION, 'fill-card')} cx={90} cy={46} r={16} strokeWidth={1.5} />
        <path
          className={ACTION}
          d="M83 42a8 8 0 0 1 13.4-3.4M97 50a8 8 0 0 1-13.4 3.4"
          strokeWidth={1.8}
        />
        <path className={ACTION} d="M97 34.5v4.5h-4.5M83 57.5V53h4.5" strokeWidth={1.8} />
      </g>
    </Vignette>
  );
}

/** De-para sem categoria: as categorias de um lado, os destinos do outro, sem decisão. */
export function MappingVignette({ className }: { className?: string }) {
  const rows = [12, 34, 56];
  return (
    <Vignette name="mapping" className={className}>
      <g {...part(0)}>
        {rows.map((y) => (
          <rect
            key={`a-${y}`}
            className={FRAME}
            x={8}
            y={y}
            width={36}
            height={12}
            rx={3}
            strokeWidth={1.5}
          />
        ))}
      </g>
      <g {...part(1)}>
        {rows.map((y) => (
          <rect
            key={`b-${y}`}
            className={FRAME}
            x={76}
            y={y}
            width={36}
            height={12}
            rx={3}
            strokeWidth={1.5}
          />
        ))}
      </g>
      <g {...part(2)}>
        <path className={ACTION} d="M46 18h28" strokeWidth={2} />
        <path className={ACTION} d="M46 40h28" strokeWidth={2} strokeDasharray="3 3" />
        <path className={LINE} d="M46 62h28" strokeWidth={2} strokeDasharray="3 3" />
      </g>
    </Vignette>
  );
}

/** Lista de clientes vazia: duas linhas de cadastro e o lugar do próximo. */
export function ClientsVignette({ className }: { className?: string }) {
  return (
    <Vignette name="clients" className={className}>
      <g {...part(0)}>
        <rect className={FRAME} x={14} y={8} width={92} height={18} rx={4} strokeWidth={1.5} />
        <circle className="fill-muted stroke-border" cx={25} cy={17} r={4.5} strokeWidth={1.5} />
        <path className={LINE} d="M36 17h44" strokeWidth={2} />
      </g>
      <g {...part(1)}>
        <rect className={FRAME} x={14} y={31} width={92} height={18} rx={4} strokeWidth={1.5} />
        <circle className="fill-muted stroke-border" cx={25} cy={40} r={4.5} strokeWidth={1.5} />
        <path className={LINE} d="M36 40h34" strokeWidth={2} />
      </g>
      <g {...part(2)}>
        <rect
          className={ACTION}
          x={14}
          y={54}
          width={92}
          height={18}
          rx={4}
          strokeWidth={1.5}
          strokeDasharray="4 3"
        />
        <path className={ACTION} d="M60 59v8M56 63h8" strokeWidth={2} />
      </g>
    </Vignette>
  );
}
