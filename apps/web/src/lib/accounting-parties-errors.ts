/**
 * As recusas da importação das relações de clientes e fornecedores do Domínio (86e3qhhrn),
 * tipadas e num lugar só.
 *
 * A importação reusa o vocabulário de recusa de arquivo (mesmos `code` da S14 e do plano),
 * com `details` PRÓPRIOS: as linhas levam o CÓDIGO da conta (`accountCode`), que é o que
 * a pessoa procura no plano para corrigir, e os motivos são outros. Por isso um leitor
 * separado do `accounting-chart-errors.ts` (ADR-047-FE), com a mesma regra: nenhum
 * conteúdo de célula chega aqui (documento, nome e razão social nunca saem do servidor).
 * Código fora da lista → `null`: o caller cai no toast com o `userMessage`.
 */
import { ApiError } from '@/lib/api/client';

export const ACCOUNTING_PARTIES_REFUSAL_CODES = [
  'FORMATO_NAO_SUPORTADO',
  'ARQUIVO_INVALIDO',
  'CABECALHO_DIVERGENTE',
  'LINHAS_INVALIDAS',
  'RELATORIO_DE_OUTRA_EMPRESA',
] as const;

export type AccountingPartiesRefusalCode = (typeof ACCOUNTING_PARTIES_REFUSAL_CODES)[number];

/**
 * Motivos de linha do leitor das relações (`client_accounting_chart/parties.py`): os que
 * RECUSAM o arquivo e os que só deixam a linha SEM vínculo. Valor fora da lista (backend
 * mais novo) sai cru, nunca some.
 */
export const PARTY_LINE_REASON_LABELS: Record<string, string> = {
  // Recusam o arquivo inteiro.
  conta_ausente: 'Linha com documento mas sem a conta (coluna Conta)',
  conta_invalida: 'A coluna Conta tem um valor que não é um número inteiro',
  linha_fora_do_bloco:
    'Pessoa depois da linha de total do relatório: o arquivo inteiro foi recusado para nenhuma ficar de fora',
  conta_nao_encontrada: 'Conta não encontrada no plano contábil do cliente',
  conta_sintetica: 'Conta sintética (só agrupa): não recebe vínculo',
  // Não recusam: a linha fica sem vínculo.
  sem_conta: 'Sem conta no Domínio (conta 0)',
  documento_ausente: 'Sem CNPJ ou CPF no Domínio',
  documento_invalido:
    'CNPJ ou CPF inválido no relatório (dígito verificador errado, ou zeros à esquerda perdidos numa célula numérica)',
  conta_compartilhada:
    'Conta compartilhada por mais de uma pessoa: o vínculo precisa ser um para um',
};

export function partyLineReasonLabel(reason: string): string {
  return PARTY_LINE_REASON_LABELS[reason] ?? reason;
}

export interface PartyInvalidLine {
  line: number;
  reason: string;
  accountCode: string | null;
}

export type AccountingPartiesRefusal =
  | { code: 'FORMATO_NAO_SUPORTADO'; userMessage: string }
  | { code: 'RELATORIO_DE_OUTRA_EMPRESA'; userMessage: string }
  | { code: 'CABECALHO_DIVERGENTE'; userMessage: string }
  | {
      code: 'ARQUIVO_INVALIDO';
      userMessage: string;
      /** `relatorio_nao_reconhecido` | `empresa_nao_identificada` | `sem_pessoas` | null. */
      reason: string | null;
    }
  | {
      code: 'LINHAS_INVALIDAS';
      userMessage: string;
      /** Até 50 linhas (o servidor recorta); `total` é quantas havia de fato. */
      lines: PartyInvalidLine[];
      total: number;
    };

function readLines(value: unknown): PartyInvalidLine[] {
  if (!Array.isArray(value)) return [];
  const lines: PartyInvalidLine[] = [];
  for (const item of value) {
    if (typeof item !== 'object' || item === null) continue;
    const { line, reason, accountCode } = item as {
      line?: unknown;
      reason?: unknown;
      accountCode?: unknown;
    };
    if (typeof line === 'number' && typeof reason === 'string') {
      lines.push({
        line,
        reason,
        accountCode: typeof accountCode === 'string' ? accountCode : null,
      });
    }
  }
  return lines;
}

/** A recusa tipada da importação da relação, ou `null` (aí o caller degrada com toast). */
export function readAccountingPartiesRefusal(error: unknown): AccountingPartiesRefusal | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  const code = ACCOUNTING_PARTIES_REFUSAL_CODES.find((candidate) => candidate === error.code);
  if (code === undefined) return null;
  const { details, userMessage } = error;
  switch (code) {
    case 'FORMATO_NAO_SUPORTADO':
    case 'RELATORIO_DE_OUTRA_EMPRESA':
    case 'CABECALHO_DIVERGENTE':
      return { code, userMessage };
    case 'ARQUIVO_INVALIDO':
      return {
        code,
        userMessage,
        reason: typeof details.reason === 'string' ? details.reason : null,
      };
    case 'LINHAS_INVALIDAS': {
      const lines = readLines(details.lines);
      const total = typeof details.total === 'number' ? details.total : lines.length;
      return { code, userMessage, lines, total };
    }
  }
}

/** Os campos da gaveta "Vincular conta" (86e3tttaf) que uma recusa do servidor aponta. */
export type PartyLinkFormField = 'accountId' | 'document';

export interface PartyLinkFormRefusal {
  field: PartyLinkFormField;
  message: string;
}

/**
 * As recusas do vínculo manual (86e3tttaf), no CAMPO certo da gaveta:
 *   - 409 `DOCUMENTO_JA_VINCULADO` → `document`, com o CÓDIGO da conta que já tem o
 *     documento (`details.accountCode`), que é o que a pessoa procura na lista;
 *   - 422 `CONTA_CONTABIL_INVALIDA` → o `details.field` do servidor (`document` no
 *     dígito errado; `accountId` na conta sintética ou inativa, corrida com a busca).
 *
 * A mensagem é a `userMessage` do servidor. Qualquer outro erro → `null`: toast.
 */
export function readPartyLinkRefusal(error: unknown): PartyLinkFormRefusal | null {
  if (!(error instanceof ApiError)) return null;
  if (error.code === 'DOCUMENTO_JA_VINCULADO') {
    const code = error.details.accountCode;
    return {
      field: 'document',
      message:
        typeof code === 'string' && code.length > 0
          ? `Este CNPJ/CPF já está vinculado à conta ${code}. Remova o vínculo dela antes, ou escolha outro documento.`
          : error.userMessage,
    };
  }
  if (error.code === 'CONTA_CONTABIL_INVALIDA') {
    const field = error.details.field;
    if (field === 'document' || field === 'accountId') {
      return { field, message: error.userMessage };
    }
  }
  return null;
}
