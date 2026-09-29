/**
 * Leitor tipado das RECUSAS do arquivo contábil — Sprint 13 (FRONT 13.6).
 *
 * O backend recusa a geração com 409 tipado e `details` estruturado (só
 * códigos e números, nunca texto do cliente). A tela mostra cada recusa como
 * ESTADO — a mensagem do servidor e o que corrigir —, nunca toast genérico.
 * Este módulo estreita o `ApiError.details` (que é `Record<string, unknown>`)
 * sem cast, como o `lib/file-origin-errors.ts` da S14:
 *
 *   - `ARQUIVO_COBERTURA_PARCIAL` / `ARQUIVO_PARTIDA_INCOMPLETA` →
 *     `details.categoryCodes: string[]`;
 *   - `ARQUIVO_PARTICAO_NAO_FECHA` → as parcelas (`competenceAmount`,
 *     `withAccountAmount`, `notMappedAmount`, `undecidedAmount`,
 *     `uncategorizedAmount`), Decimal em string;
 *   - `ARQUIVO_TEXTO_NAO_CABE` → `details.categories: [{categoryCode, field,
 *     reason}]`, `reason` de vocabulário fechado;
 *   - `ARQUIVO_SEM_MATERIALIZACAO` e `ARQUIVO_DESTINO_INVALIDO` → sem details.
 *
 * Qualquer outro erro vira `kind: 'other'` com o `userMessage` do servidor (ou
 * a mensagem de rede): a tela também o mostra como estado, com a mesma caixa.
 */
import { ApiError, NetworkError } from '@/lib/api/client';

export const ACCOUNTING_FILE_REFUSAL_CODES = [
  'ARQUIVO_COBERTURA_PARCIAL',
  'ARQUIVO_PARTIDA_INCOMPLETA',
  'ARQUIVO_PARTICAO_NAO_FECHA',
  'ARQUIVO_TEXTO_NAO_CABE',
  'ARQUIVO_SEM_MATERIALIZACAO',
  'ARQUIVO_DESTINO_INVALIDO',
] as const;

export type AccountingFileRefusalCode = (typeof ACCOUNTING_FILE_REFUSAL_CODES)[number];

/** As parcelas da identidade de partição, na ordem em que a tela as mostra. */
export const PARTITION_PARCELS = [
  { key: 'competenceAmount', label: 'Total da competência' },
  { key: 'withAccountAmount', label: 'Com conta (entra no arquivo)' },
  { key: 'notMappedAmount', label: 'Não mapear' },
  { key: 'undecidedAmount', label: 'Sem decisão' },
  { key: 'uncategorizedAmount', label: 'Sem categoria de origem' },
] as const;

export interface PartitionParcel {
  key: string;
  label: string;
  /** Decimal em string (reais), como o servidor mandou. */
  amount: string;
}

export interface TextProblem {
  categoryCode: string;
  field: string;
  reason: string;
}

export type AccountingFileRefusal =
  | {
      kind: 'refusal';
      code: AccountingFileRefusalCode;
      userMessage: string;
      /** Códigos das categorias a corrigir (vazio quando a recusa não nomeia). */
      categoryCodes: string[];
      parcels: PartitionParcel[];
      textProblems: TextProblem[];
    }
  | { kind: 'other'; code: string; userMessage: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
}

function readTextProblems(value: unknown): TextProblem[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) =>
    isRecord(item) &&
    typeof item.categoryCode === 'string' &&
    typeof item.field === 'string' &&
    typeof item.reason === 'string'
      ? [{ categoryCode: item.categoryCode, field: item.field, reason: item.reason }]
      : [],
  );
}

function readParcels(details: Record<string, unknown>): PartitionParcel[] {
  return PARTITION_PARCELS.flatMap(({ key, label }) => {
    const amount = details[key];
    return typeof amount === 'string' || typeof amount === 'number'
      ? [{ key, label, amount: String(amount) }]
      : [];
  });
}

function isRefusalCode(code: string): code is AccountingFileRefusalCode {
  return (ACCOUNTING_FILE_REFUSAL_CODES as readonly string[]).includes(code);
}

export function readAccountingFileRefusal(error: unknown): AccountingFileRefusal {
  if (error instanceof ApiError && isRefusalCode(error.code)) {
    const textProblems = readTextProblems(error.details.categories);
    const categoryCodes =
      error.code === 'ARQUIVO_TEXTO_NAO_CABE'
        ? [...new Set(textProblems.map((p) => p.categoryCode))]
        : readStrings(error.details.categoryCodes);
    return {
      kind: 'refusal',
      code: error.code,
      userMessage: error.userMessage,
      categoryCodes,
      parcels: error.code === 'ARQUIVO_PARTICAO_NAO_FECHA' ? readParcels(error.details) : [],
      textProblems,
    };
  }
  if (error instanceof ApiError) {
    return { kind: 'other', code: error.code, userMessage: error.userMessage };
  }
  if (error instanceof NetworkError) {
    return { kind: 'other', code: 'NETWORK', userMessage: error.userMessage };
  }
  return {
    kind: 'other',
    code: 'UNKNOWN',
    userMessage: 'Não foi possível gerar o arquivo. Tente novamente.',
  };
}

const TEXT_REASON_LABELS: Record<string, string> = {
  quebra_de_linha: 'tem quebra de linha',
  contem_separador: 'contém o separador de colunas do arquivo',
  inicio_de_formula: 'começa com =, +, - ou @ (seria lido como fórmula)',
  fora_da_codificacao: 'tem caractere que a codificação do arquivo não aceita (ex.: € ou —)',
};

/** Motivo fora do vocabulário conhecido aparece cru — nunca some. */
export function textReasonLabel(reason: string): string {
  return TEXT_REASON_LABELS[reason] ?? reason;
}

/** O título do estado, por recusa: diz O QUE impediu, antes da mensagem do servidor. */
export const REFUSAL_TITLES: Record<AccountingFileRefusalCode, string> = {
  ARQUIVO_COBERTURA_PARCIAL: 'Arquivo não gerado: a versão tem categorias sem decisão',
  ARQUIVO_PARTIDA_INCOMPLETA: 'Arquivo não gerado: há partidas incompletas',
  ARQUIVO_PARTICAO_NAO_FECHA: 'Arquivo não gerado: os totais da versão não fecham',
  ARQUIVO_TEXTO_NAO_CABE: 'Arquivo não gerado: há textos que não cabem no formato',
  ARQUIVO_SEM_MATERIALIZACAO: 'Arquivo não gerado: a competência não tem versão materializada',
  ARQUIVO_DESTINO_INVALIDO: 'Arquivo não gerado: a versão não é do destino Conta contábil',
};
