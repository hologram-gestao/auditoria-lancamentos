/**
 * CPF na tela (86e3tttaf, vínculo manual da conta contábil): a mesma validação do
 * servidor.
 *
 * **Espelho declarado** de `apps/api/app/core/cpf.py::normalize_cpf`: só dígitos e a
 * máscara usual (`000.000.000-00`), 11 dígitos, não todos iguais, os dois dígitos
 * verificadores certos. A tela confere antes de enviar; quem decide é o servidor.
 *
 * **CPF é dado do cliente final (§4.5).** Nada aqui loga.
 */

const CPF_DIGITS = 11;

function checkDigit(digits: string, firstWeight: number): number {
  let sum = 0;
  for (let i = 0; i < digits.length; i += 1) {
    sum += Number(digits[i]) * (firstWeight - i);
  }
  const remainder = (sum * 10) % 11;
  return remainder === 10 ? 0 : remainder;
}

/** O CPF (com ou sem máscara) é válido pela mesma regra do servidor. */
export function isValidCpf(raw: string): boolean {
  if (!/^[\d.\-\s]*$/.test(raw)) return false;
  const digits = raw.replace(/\D/g, '');
  if (digits.length !== CPF_DIGITS) return false;
  if (new Set(digits).size === 1) return false;
  const first = checkDigit(digits.slice(0, 9), 10);
  const second = checkDigit(digits.slice(0, 10), 11);
  return digits.slice(9) === `${first}${second}`;
}
