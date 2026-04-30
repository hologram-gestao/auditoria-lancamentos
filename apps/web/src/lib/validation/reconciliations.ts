/**
 * Schemas Zod do formulário "Nova Conciliação" — Doc §11.1.
 *
 * Esta sessão entrega APENAS o `[FRONT 5.1]`. Validações de hash SHA-256,
 * tamanho máximo (20 MB) e magic bytes são da próxima tarefa (`[FRONT 6.1]`)
 * e não devem ser implementadas aqui.
 *
 * Convenções do projeto:
 *   - Mensagens em PT-BR (UI-facing).
 *   - Schemas estritos no input — `z.coerce.number()` aceita o `string` que
 *     vem do `<select>` controlado pelo RHF e converte na validação.
 *   - `instanceof(File)` exige um `File` (não `FileList`); o componente de
 *     upload precisa entregar `files[0]` ao RHF (ver `file-input-field`).
 */
import { z } from 'zod';

export const ALLOWED_EXTENSIONS = ['pdf', 'csv', 'xls', 'xlsx'] as const;
export const TOLERANCE_OPTIONS = [1, 2, 3, 5, 7] as const;
export const DEFAULT_TOLERANCE = 3;

export type AllowedExtension = (typeof ALLOWED_EXTENSIONS)[number];

/** Mês corrente em formato `YYYY-MM` na timezone do navegador. */
export function currentMonth(now: Date = new Date()): string {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  return `${year}-${month}`;
}

/** Verdadeiro se `value` (`YYYY-MM`) é menor ou igual ao mês corrente. */
function notInFuture(value: string): boolean {
  return value <= currentMonth();
}

/** Extrai a extensão (sem ponto, lowercase) e checa contra a allowlist. */
function hasAllowedExtension(file: File): boolean {
  const ext = file.name.split('.').pop()?.toLowerCase();
  if (!ext) return false;
  return (ALLOWED_EXTENSIONS as readonly string[]).includes(ext);
}

export const newReconciliationSchema = z.object({
  omie_conta_id: z.coerce
    .number({ invalid_type_error: 'Selecione uma conta bancária.' })
    .int()
    .positive({ message: 'Selecione uma conta bancária.' }),
  reference_month: z
    .string()
    .regex(/^\d{4}-\d{2}$/, 'Selecione o mês de referência.')
    .refine(notInFuture, { message: 'O mês de referência não pode ser futuro.' }),
  tolerance_days: z.coerce
    .number({ invalid_type_error: 'Selecione uma tolerância válida.' })
    .int()
    .refine((v) => (TOLERANCE_OPTIONS as readonly number[]).includes(v), {
      message: 'Tolerância inválida.',
    }),
  file: z
    .instanceof(File, { message: 'Selecione um arquivo.' })
    .refine((f) => f.size > 0, { message: 'O arquivo está vazio.' })
    .refine(hasAllowedExtension, {
      message: `Extensão não suportada. Use: ${ALLOWED_EXTENSIONS.join(', ').toUpperCase()}.`,
    }),
});

export type NewReconciliationFormValues = z.infer<typeof newReconciliationSchema>;
