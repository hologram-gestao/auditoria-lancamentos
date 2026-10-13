/**
 * CNPJ na tela (86e3n70q0, bloco C): máscara de digitação e a mesma validação do
 * servidor.
 *
 * **Espelho declarado** de `apps/api/app/core/cnpj.py::normalize_cnpj`: 14 dígitos,
 * não todos iguais, os dois dígitos verificadores certos. A tela confere antes de
 * buscar para não gastar uma ida ao Omie num 400 que ela já sabia — quem decide
 * continua sendo o servidor.
 *
 * **CNPJ é dado do cliente final (§4.5).** Nada aqui loga; o valor vai só no
 * pedido ao servidor, que também não o persiste.
 */

const CNPJ_DIGITS = 14;
const WEIGHTS_FIRST = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2];
const WEIGHTS_SECOND = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2];

/** Só os dígitos, no máximo 14 (o que passar da conta é descartado na digitação). */
export function cnpjDigits(raw: string): string {
  return raw.replace(/\D/g, '').slice(0, CNPJ_DIGITS);
}

/** Máscara progressiva `00.000.000/0000-00` sobre o que já foi digitado. */
export function formatCnpj(raw: string): string {
  const d = cnpjDigits(raw);
  let out = d.slice(0, 2);
  if (d.length > 2) out += `.${d.slice(2, 5)}`;
  if (d.length > 5) out += `.${d.slice(5, 8)}`;
  if (d.length > 8) out += `/${d.slice(8, 12)}`;
  if (d.length > 12) out += `-${d.slice(12, 14)}`;
  return out;
}

function checkDigit(digits: string, weights: number[]): number {
  const sum = weights.reduce((acc, w, i) => acc + Number(digits[i]) * w, 0);
  const remainder = sum % 11;
  return remainder < 2 ? 0 : 11 - remainder;
}

/** O CNPJ (com ou sem máscara) é válido pela mesma regra do servidor. */
export function isValidCnpj(raw: string): boolean {
  if (!/^[\d./\-\s]*$/.test(raw)) return false;
  const digits = raw.replace(/\D/g, '');
  if (digits.length !== CNPJ_DIGITS) return false;
  if (new Set(digits).size === 1) return false;
  const first = checkDigit(digits.slice(0, 12), WEIGHTS_FIRST);
  const second = checkDigit(digits.slice(0, 12) + String(first), WEIGHTS_SECOND);
  return digits.slice(12) === `${first}${second}`;
}
