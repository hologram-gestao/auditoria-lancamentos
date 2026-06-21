/**
 * Schemas Zod do formulário "Nova Conciliação" — Doc §11.1.
 *
 * `[FRONT 5.1]` entregou os campos. `[FRONT 6.1]` adiciona o limite de tamanho
 * (20 MB). Magic bytes ficam no servidor (S9) — confiança em validação
 * client-side é proibida pela CLAUDE.md §3.8.
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
/** Limite duro alinhado ao backend (Doc §11.3 V2). 20 MB = 20 * 1024 * 1024 bytes. */
export const MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024;
export const MAX_FILE_SIZE_LABEL = '20 MB';

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
  // FASE 1 (BACK 1.6): tolerância de data deixou de ser parametrizável — é
  // fixa no backend. O campo saiu do formulário (FRONT 1.4) e do request.
  // Ordem dos refines importa: vazio → tamanho → extensão. Cada refine só
  // dispara se o anterior passou (zod retorna no primeiro erro), então o
  // usuário sempre vê a falha mais "fundamental" primeiro.
  file: z
    .instanceof(File, { message: 'Selecione um arquivo.' })
    .refine((f) => f.size > 0, { message: 'O arquivo está vazio.' })
    .refine((f) => f.size <= MAX_FILE_SIZE_BYTES, {
      message: `Arquivo excede o limite de ${MAX_FILE_SIZE_LABEL}.`,
    })
    .refine(hasAllowedExtension, {
      message: `Extensão não suportada. Use: ${ALLOWED_EXTENSIONS.join(', ').toUpperCase()}.`,
    }),
});

export type NewReconciliationFormValues = z.infer<typeof newReconciliationSchema>;
