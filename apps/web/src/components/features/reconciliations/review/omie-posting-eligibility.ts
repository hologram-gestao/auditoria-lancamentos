/**
 * Quem pode ser lançada no Omie (Sprint 7 / FRONT 07.6 · R1).
 *
 * **Espelho declarado do servidor**, não uma segunda regra: a ordem e as
 * mensagens são as de `_eligibility_block`
 * (`apps/api/app/modules/reconciliations/omie_posting/service.py`) e de
 * `OmiePostingNotEligibleError`. A UI **não é barreira** — o backend recusa de
 * qualquer forma —, mas oferecer uma ação que o servidor nega é defeito
 * (CLAUDE.md §4.9), e por isso a regra mora num lugar só e é testada.
 *
 * Os motivos são `Extract<>` do enum do contrato: se o backend renomear um
 * deles, isto para de compilar em vez de virar copy divergente na tela. O
 * único motivo local é `sessao_nao_e_cartao` — no servidor ele não é motivo de
 * LINHA, é um erro do lote inteiro (400), e aqui vira o silêncio da coluna.
 */
import type { FileEntryItem } from '@/lib/api/reconciliations';
import type { OmiePostingLineReason } from '@/lib/contracts';

export type PostingBlockReason =
  | Extract<
      OmiePostingLineReason,
      'linha_ignorada' | 'ja_lancada' | 'nao_e_sem_omie' | 'estorno_nao_verificado'
    >
  | 'sessao_nao_e_cartao';

/**
 * Mensagens VERBATIM do backend (mesmo texto que voltaria no resumo do lote).
 * Duas cópias do mesmo motivo com palavras diferentes fariam o operador achar
 * que são coisas distintas.
 */
export const POSTING_BLOCK_MESSAGE: Record<PostingBlockReason, string> = {
  linha_ignorada: 'Linha ignorada na revisão — não é lançada.',
  ja_lancada: 'Esta linha já está vinculada a um lançamento do Omie.',
  nao_e_sem_omie: 'Só compras sem correspondente no Omie podem ser lançadas.',
  estorno_nao_verificado:
    'Estornos ainda não podem ser lançados: a representação de crédito no Omie ainda não foi verificada. Lance apenas as compras.',
  sessao_nao_e_cartao:
    'Só é possível lançar no Omie a partir de uma conciliação de cartão de crédito.',
};

/**
 * `null` = a linha pode ser lançada. Qualquer outro valor é o motivo do
 * bloqueio, na MESMA ordem de precedência do servidor: ignorada vence "já
 * lançada", que vence "não é sem Omie", que vence o estorno. A ordem importa
 * porque é ela que decide qual motivo o operador lê quando dois valem ao
 * mesmo tempo.
 *
 * Estorno (valor positivo): o contrato real do `IncluirLancCC` não tem campo
 * de sinal e a representação do crédito segue não-verificada (S-1) — o
 * servidor bloqueia, então a UI não oferece (§4.9).
 */
export function getPostingBlock(
  entry: Pick<FileEntryItem, 'situation' | 'omie_lancamento_id' | 'amount'>,
  options: { isCard: boolean },
): PostingBlockReason | null {
  if (!options.isCard) return 'sessao_nao_e_cartao';
  if (entry.situation === 'ignorado') return 'linha_ignorada';
  if (entry.omie_lancamento_id !== null) return 'ja_lancada';
  if (entry.situation !== 'sem_omie') return 'nao_e_sem_omie';
  if (Number(entry.amount) > 0) return 'estorno_nao_verificado';
  return null;
}

/** Açúcar para filtros/contadores — mesma decisão, sem repetir o `=== null`. */
export function isPostingEligible(
  entry: Pick<FileEntryItem, 'situation' | 'omie_lancamento_id' | 'amount'>,
  options: { isCard: boolean },
): boolean {
  return getPostingBlock(entry, options) === null;
}
