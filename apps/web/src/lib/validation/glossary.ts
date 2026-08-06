/**
 * Schemas Zod do glossário do tenant — espelham
 * `apps/api/app/modules/glossary/schemas.py` (`_GlossaryEntryWrite`) 1:1.
 *
 * Limites copiados do backend, campo a campo (constantes de
 * `app/db/models/client_glossary_entry.py`, lidas antes de escrever):
 *   - `kind`: `categoria` | `fornecedor` | `regra` (enum fechado)
 *   - `name`: obrigatório, 1..120 — o servidor faz `.strip()` e recusa
 *     só-espaços, então o `trim()` daqui reproduz a mesma regra em vez de
 *     deixar o 422 explicar
 *   - `code`: opcional, até 40
 *   - `description`: opcional, até 500
 *
 * A validação do formulário é **UX**, não barreira: a autoridade é o servidor
 * (que revalida tudo). O que ela evita é o round-trip que devolve 422 para algo
 * que dava para dizer antes.
 */
import { z } from 'zod';

import type { GlossaryEntryKind } from '@/lib/contracts';

/** Mesmas constantes do backend (`MAX_*_CHARS`). */
export const GLOSSARY_MAX_CODE_CHARS = 40;
export const GLOSSARY_MAX_NAME_CHARS = 120;
export const GLOSSARY_MAX_DESCRIPTION_CHARS = 500;
/** `MAX_ENTRIES_PER_CLIENT` — o teto que devolve `GLOSSARY_LIMIT_EXCEEDED`. */
export const GLOSSARY_MAX_ENTRIES = 200;

export const glossaryKindSchema = z.enum(['categoria', 'fornecedor', 'regra']);

export type GlossaryKindFormValue = z.infer<typeof glossaryKindSchema>;

/**
 * Trava de contrato bidirecional: tipo novo (ou renomeado) no backend derruba a
 * compilação aqui. Checar só a atribuição não bastaria — deixaria passar um
 * `kind` do contrato que o formulário esqueceu de oferecer, e a entrada ficaria
 * inalcançável pela tela sem ninguém perceber.
 */
type AssertSameUnion<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;
const kindUnionMatchesContract: AssertSameUnion<GlossaryKindFormValue, GlossaryEntryKind> = true;
void kindUnionMatchesContract;

/** Rótulos PT-BR — fonte única para select, tabela, badges e mensagens. */
export const GLOSSARY_KIND_LABELS: Record<GlossaryKindFormValue, string> = {
  categoria: 'Categoria',
  fornecedor: 'Fornecedor típico',
  regra: 'Regra de auditoria',
};

/** Ajuda de campo por tipo — o que a pessoa deve escrever em "nome". */
export const GLOSSARY_KIND_HINTS: Record<GlossaryKindFormValue, string> = {
  categoria: 'Ex.: "Taxas bancárias". Use a descrição para explicar quando ela se aplica.',
  fornecedor: 'Ex.: "Moinho Prado". Use a descrição para dizer o que costuma ser lançado.',
  regra: 'Ex.: "IOF nunca é classificado como juros".',
};

export const glossaryEntrySchema = z.object({
  kind: glossaryKindSchema,
  name: z
    .string()
    .trim()
    .min(1, 'Informe o nome.')
    .max(GLOSSARY_MAX_NAME_CHARS, `Nome muito longo (máx. ${GLOSSARY_MAX_NAME_CHARS}).`),
  code: z
    .string()
    .trim()
    .max(GLOSSARY_MAX_CODE_CHARS, `Código muito longo (máx. ${GLOSSARY_MAX_CODE_CHARS}).`),
  description: z
    .string()
    .trim()
    .max(
      GLOSSARY_MAX_DESCRIPTION_CHARS,
      `Descrição muito longa (máx. ${GLOSSARY_MAX_DESCRIPTION_CHARS}).`,
    ),
});

export type GlossaryFormValues = z.infer<typeof glossaryEntrySchema>;

/**
 * O formulário guarda string vazia (um `<input>` controlado não tem `null`); o
 * contrato quer `null` para "sem valor". A conversão mora aqui, num lugar só —
 * mandar `""` faria o servidor gravar um código vazio em vez de nenhum.
 */
export function emptyToNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}
