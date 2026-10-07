/**
 * Dinheiro com sinal e cor (86e3k1q30): o ÚNICO lugar que decide como um valor
 * monetário aparece quando o sinal ou a situação importam.
 *
 * Dois eixos, nunca misturados:
 *   - o **sinal** vem do VALOR. Com `tone="sign"` ele é explícito: `+` para
 *     positivo e o menos tipográfico `−` (U+2212) para negativo, ambos
 *     montados sobre `formatBRL(|valor|)`. Zero nunca ganha sinal;
 *   - a **cor** vem do `tone`: `sign` colore pelo sinal (positivo `success`,
 *     negativo `destructive`), `overdue` e `warning` pela situação, `neutral`
 *     não colore.
 *
 * Cor nunca é o único aviso: no `sign` o sinal acompanha, e nos tons de
 * situação o rótulo da célula ("Vencido", "90+ dias") diz o que o vermelho
 * quer dizer. `formatBRL` não muda: fora do `sign`, um negativo mostra o hífen
 * de sempre, sem cor.
 *
 * `className` vem por ÚLTIMO no `cn` (tailwind-merge): um pai que precisa de
 * outra cor (o botão ativo da carteira, em `accent-foreground`) sobrescreve o
 * tom sem par de contraste novo.
 */
import { formatBRL } from '@/lib/format';
import { cn } from '@/lib/utils';

export type MoneyTone = 'sign' | 'neutral' | 'overdue' | 'warning';

const MINUS_SIGN = '\u2212';

/** O valor como número, ou `null` quando não é um número finito. */
function toNumber(value: string | number): number | null {
  const num = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(num) ? num : null;
}

/**
 * Sinal no nível do CENTAVO: `-0,004` aparece como `R$ 0,00` e, portanto, sem
 * sinal nem cor. Comparar o número cru imprimiria `−R$ 0,00` em vermelho.
 */
function signOf(num: number): -1 | 0 | 1 {
  const cents = Math.round(num * 100);
  if (cents > 0) return 1;
  if (cents < 0) return -1;
  return 0;
}

/** A classe de cor de um valor no tom pedido (função pura). */
export function moneyToneClass(value: string | number, tone: MoneyTone): string {
  switch (tone) {
    case 'sign': {
      const num = toNumber(value);
      if (num === null) return '';
      const sign = signOf(num);
      if (sign > 0) return 'text-success';
      if (sign < 0) return 'text-destructive';
      return '';
    }
    case 'overdue':
      return 'text-destructive';
    case 'warning':
      return 'text-warning';
    case 'neutral':
      return '';
  }
}

/** O texto do valor: no `sign`, sinal explícito; nos demais, o `formatBRL` de sempre. */
export function formatMoney(value: string | number, tone: MoneyTone): string {
  const num = toNumber(value);
  if (num === null) return formatBRL(value);
  if (tone !== 'sign') return formatBRL(num);
  const sign = signOf(num);
  const absolute = formatBRL(Math.abs(num));
  if (sign > 0) return `+${absolute}`;
  if (sign < 0) return `${MINUS_SIGN}${absolute}`;
  return formatBRL(0);
}

interface MoneyProps {
  value: string | number;
  tone?: MoneyTone;
  className?: string;
}

export function Money({ value, tone = 'neutral', className }: MoneyProps) {
  const invalid = toNumber(value) === null;
  return (
    <span
      className={cn(
        'whitespace-nowrap tabular-nums',
        invalid ? 'text-muted-foreground' : moneyToneClass(value, tone),
        className,
      )}
    >
      {formatMoney(value, tone)}
    </span>
  );
}
