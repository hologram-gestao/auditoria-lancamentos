/**
 * Check que se desenha (86e3h579d): o `.lp-check` da confirmação do formulário da
 * landing virou componente, e o toast de sucesso do app usa o mesmo.
 *
 * Um círculo e um traço, em `currentColor` (quem chama decide a cor por token:
 * `text-success`). O desenho é a classe `animated-check` do `globals.css`: o círculo
 * em 700 ms e o traço em 600 ms, 150 ms depois, uma vez só e sem loop. A animação
 * mora SÓ sob `prefers-reduced-motion: no-preference`; com movimento reduzido o check
 * nasce desenhado. Decorativo (`aria-hidden`): o texto ao lado é quem diz o que
 * aconteceu.
 *
 * A geometria (viewBox 56, raio 25, o traço) é a da landing, que não podia mudar; num
 * ícone pequeno (16 px no toast) o traço fica fino, e quem chama engrossa por
 * `strokeWidth`.
 */
import { cn } from '@/lib/utils';

export function AnimatedCheck({
  className,
  strokeWidth = 3,
}: {
  className?: string;
  strokeWidth?: number;
}) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 56 56"
      className={cn('animated-check', className)}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="28" cy="28" r="25" />
      <path d="M17 29l7 7 15-16" />
    </svg>
  );
}
