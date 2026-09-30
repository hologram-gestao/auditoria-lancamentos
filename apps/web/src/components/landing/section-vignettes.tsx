/**
 * Vinheta da seção "Segurança" (86e3gwzj0), no espírito do card do hero: SVG puro,
 * decorativo (`aria-hidden`), ao lado do título do bloco (`LandingSection aside`).
 * A de "Para quem" (três avatares) saiu na 86e3h0xcr.
 *
 * Regras: no máximo 20 elementos SVG (um teste conta), cor só por token ou pela cor
 * da marca (classes `.lp-vig__*` no `landing.css`), entrada de 600 a 900 ms quando o
 * título é revelado e depois PARADA, sem loop. Sob movimento reduzido, estado final.
 */

/**
 * "Segurança": um cadeado que fecha, uma chave que gira 90 graus e, abaixo, três
 * blocos de dado que ganham hachura (cifrados).
 */
export function SecurityVignette() {
  const blocks = [20, 86, 152];
  return (
    <svg
      aria-hidden="true"
      data-lp-vignette="security"
      viewBox="0 0 200 124"
      className="lp-vig h-auto w-52"
    >
      <defs>
        <pattern
          id="lp-vig-hatch"
          width="6"
          height="6"
          patternUnits="userSpaceOnUse"
          patternTransform="rotate(45)"
        >
          <path className="lp-vig__hatch-line" d="M0 0V6" />
        </pattern>
      </defs>
      <path className="lp-vig__shackle" d="M34 54V40a14 14 0 0 1 28 0v14" />
      <rect className="lp-vig__lock" x={22} y={52} width={52} height={40} rx={8} />
      <circle className="lp-vig__keyhole" cx={48} cy={70} r={5} />
      <g className="lp-vig__key">
        <circle cx={112} cy={62} r={10} />
        <rect x={120} y={59} width={36} height={6} rx={2} />
        <rect x={146} y={63} width={6} height={10} rx={1.5} />
      </g>
      {blocks.map((x) => (
        <rect
          key={`block-${x}`}
          className="lp-vig__block"
          x={x}
          y={100}
          width={48}
          height={20}
          rx={4}
        />
      ))}
      {blocks.map((x, index) => (
        <rect
          key={`hatch-${x}`}
          className="lp-vig__cipher"
          style={{ '--lp-vig-i': index } as React.CSSProperties}
          x={x}
          y={100}
          width={48}
          height={20}
          rx={4}
          fill="url(#lp-vig-hatch)"
        />
      ))}
    </svg>
  );
}
