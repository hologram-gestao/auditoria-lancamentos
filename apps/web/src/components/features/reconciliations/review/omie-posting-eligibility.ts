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
import type {
  ClientConnection,
  OmiePostingLineReason,
  OmiePostingMode,
  OmiePostingState,
} from '@/lib/contracts';

/**
 * A origem do cliente sabe ESCREVER? (Sprint 9 / R3 · R6)
 *
 * Espelho literal do predicado único do servidor
 * (`modules/client_connections/capability.py::connection_supports`): **ativa E
 * o tipo declara a capacidade**. `inativa` e `erro` não são capazes.
 *
 * Capacidade é DADO (vem em `connections[].capabilities`), e é por isso que a
 * UI consegue esconder a ação em vez de oferecê-la e receber
 * `CAPACIDADE_AUSENTE` depois de o operador montar o lote. Quem decide continua
 * sendo o backend — aqui só se reflete.
 */
export function originCanWrite(connections: readonly ClientConnection[]): boolean {
  return connections.some((c) => c.status === 'ativa' && c.capabilities.includes('escrever'));
}

/**
 * O processo do cartão desta conciliação permite o lançamento DIRETO? (86e3n70p0)
 *
 * Espelho do motivo `direto_indisponivel_no_modo_vencimento` do servidor: o
 * `IncluirLancCC` grava a data da COMPRA, e o cliente que lança em lote no
 * vencimento da fatura não usa essa data. Nesse processo a gaveta oferece só a
 * PROVISÃO no Contas a Pagar (86e3n70q0, bloco C), que põe o vencimento da fatura
 * no título. Sessão antiga (sem o processo gravado) é o processo de sempre.
 */
export function processAllowsDirectPosting(session: {
  card_posting_date_mode?: string | null;
}): boolean {
  return session.card_posting_date_mode !== 'invoice_due_date';
}

/**
 * VERBATIM de `DIRECT_IN_DUE_DATE_PROCESS_MESSAGE`
 * (`apps/api/app/modules/reconciliations/omie_posting/service.py`): o texto que o
 * servidor devolveria ao recusar o direto neste processo.
 */
export const DIRECT_IN_DUE_DATE_PROCESS_MESSAGE =
  'Esta conciliação cruza as compras pelo vencimento da fatura: o lançamento direto gravaria a data da compra. Provisione a compra no Contas a Pagar.';

/**
 * VERBATIM de `_MODE_LOCKED_MSG` do mesmo arquivo (motivo `modo_divergente`).
 */
export const MODE_LOCKED_MESSAGE =
  'Esta compra já teve um envio ao Omie na outra forma de lançamento. Continue por ela, ou confira no Omie antes: trocar agora poderia lançar a mesma compra duas vezes.';

/** A forma da intenção gravada, na palavra do pedido (`provisao` → `provisionar`). */
export function postingModeOf(posting: Pick<OmiePostingState, 'kind'>): OmiePostingMode {
  return posting.kind === 'provisao' ? 'provisionar' : 'direto';
}

/**
 * Trocar de forma é permitido? Espelho de `_kind_switch_allowed`
 * (`apps/api/app/modules/reconciliations/omie_posting/service.py`): só quando
 * NENHUM dinheiro pode ter sido escrito no Omie.
 *
 * - provisão ainda no passo `fornecedor`: no máximo um cadastro de fornecedor
 *   existiu, nunca título nem baixa;
 * - direto recusado por `faultstring` na ÚNICA tentativa (`OMIE_FAULT`, sem
 *   lançamento vinculado).
 *
 * Fora disso o servidor responde `modo_divergente`, e a gaveta abre a linha
 * TRAVADA na forma gravada.
 */
export function kindSwitchAllowed(
  posting: Pick<OmiePostingState, 'kind' | 'status' | 'step' | 'attempts' | 'error_code'> &
    Pick<OmiePostingState, 'omie_lancamento_id'>,
): boolean {
  if (posting.status === 'confirmed') return false;
  if (posting.kind === 'provisao') return posting.step === 'fornecedor';
  return (
    posting.status === 'failed' &&
    posting.attempts === 1 &&
    posting.error_code === 'OMIE_FAULT' &&
    posting.omie_lancamento_id === null
  );
}

/** A forma em que a linha está TRAVADA (`null` = a pessoa escolhe). */
export function lockedPostingMode(
  posting: OmiePostingState | null | undefined,
): OmiePostingMode | null {
  if (posting === null || posting === undefined) return null;
  return kindSwitchAllowed(posting) ? null : postingModeOf(posting);
}

/**
 * Provisão que começou e não terminou (`pending` ou `failed`): a linha mostra
 * "Lançamento parado" e a ação vira "Retomar lançamento".
 */
export function isProvisionStalled(posting: OmiePostingState | null | undefined): boolean {
  return (
    posting !== null &&
    posting !== undefined &&
    posting.kind === 'provisao' &&
    posting.status !== 'confirmed'
  );
}

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

type PostingEntry = Pick<FileEntryItem, 'situation' | 'omie_lancamento_id' | 'amount'> & {
  omie_posting?: OmiePostingState | null;
};

/**
 * `null` = a linha pode ser lançada. Qualquer outro valor é o motivo do
 * bloqueio, na MESMA ordem de precedência do servidor (`_eligibility_block`,
 * depois o estado da intenção): ignorada vence "já lançada", que vence "não é
 * sem Omie", que vence o estorno, que vence a intenção CONFIRMADA. A ordem
 * importa porque é ela que decide qual motivo o operador lê quando dois valem
 * ao mesmo tempo.
 *
 * A intenção confirmada (86e3n70q0, bloco C) é o que pega a PROVISÃO: ela não
 * casa a linha, que continua `sem_omie` sem `omie_lancamento_id` — só o
 * `omie_posting` vindo do servidor diz que o título existe.
 *
 * Estorno (valor positivo): o contrato real do `IncluirLancCC` não tem campo
 * de sinal e a representação do crédito segue não-verificada (S-1) — o
 * servidor bloqueia, então a UI não oferece (§4.9).
 */
export function getPostingBlock(
  entry: PostingEntry,
  options: { isCard: boolean },
): PostingBlockReason | null {
  if (!options.isCard) return 'sessao_nao_e_cartao';
  if (entry.situation === 'ignorado') return 'linha_ignorada';
  if (entry.omie_lancamento_id !== null) return 'ja_lancada';
  if (entry.situation !== 'sem_omie') return 'nao_e_sem_omie';
  if (Number(entry.amount) > 0) return 'estorno_nao_verificado';
  if (entry.omie_posting?.status === 'confirmed') return 'ja_lancada';
  return null;
}

/** Açúcar para filtros/contadores — mesma decisão, sem repetir o `=== null`. */
export function isPostingEligible(entry: PostingEntry, options: { isCard: boolean }): boolean {
  return getPostingBlock(entry, options) === null;
}

/**
 * O texto lido do bloqueio. Igual ao `POSTING_BLOCK_MESSAGE`, salvo a provisão
 * concluída, que o servidor descreve com o número do título
 * (`_provision_already_done`).
 */
export function postingBlockMessage(
  block: PostingBlockReason,
  posting?: OmiePostingState | null,
): string {
  if (block === 'ja_lancada' && posting?.kind === 'provisao' && posting.status === 'confirmed') {
    return `Esta compra já foi provisionada no Omie (título nº ${posting.payable_id}).`;
  }
  return POSTING_BLOCK_MESSAGE[block];
}
