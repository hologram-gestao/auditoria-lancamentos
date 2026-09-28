/**
 * As recusas do ARQUIVO, tipadas e num lugar só (Sprint 14 / R2 · R5).
 *
 * O R5 exige que toda recusa apresente o motivo ESPECÍFICO e acionável — a
 * coluna divergente nomeada, a linha inválida com número e motivo, os dois
 * totais lado a lado — e "nunca um erro genérico". O servidor cumpre a metade
 * dele com exceções tipadas (`code` + `userMessage` + `details`,
 * `core/exceptions.py`); este módulo cumpre a outra: lê o `details` cru
 * (`Record<string, unknown>`) e devolve um valor ESTREITADO que a tela
 * ramifica por `code`. Código fora desta lista → `null`, e o caller cai no
 * toast genérico com o `userMessage` — o único caso em que isso é aceitável.
 *
 * **Nenhum conteúdo de célula chega aqui.** O servidor manda nomes de coluna
 * (estrutura), números de linha e motivos de vocabulário FECHADO — e este
 * leitor só repassa o que consegue tipar; o resto é descartado, não exibido.
 *
 * Os `code` são os de `ErrorCode` (backend), MAIÚSCULOS como viajam no corpo —
 * nunca o `sem_mapeamento` minúsculo do PRD (mesma lição de `origin-state.ts`).
 */
import { ApiError } from '@/lib/api/client';

/** Os oito códigos de recusa da ingestão (BACK 14.3), na ordem em que o servidor os checa. */
export const FILE_REFUSAL_CODES = [
  'SEM_MAPEAMENTO',
  'SINAL_NAO_DECLARADO',
  'FORMATO_NAO_SUPORTADO',
  'ARQUIVO_JA_PROCESSADO',
  'CABECALHO_DIVERGENTE',
  'LINHAS_INVALIDAS',
  'TOTAL_DIVERGENTE',
  'ARQUIVO_INVALIDO',
] as const;

export type FileRefusalCode = (typeof FILE_REFUSAL_CODES)[number];

/**
 * Motivos de linha inválida — `LineReason` do leitor do backend
 * (`client_file_ingestion/reader.py`), vocabulário FECHADO. Valor fora dele
 * (backend mais novo que o front) sai com o texto cru, nunca derruba a linha.
 */
export const INVALID_LINE_REASON_LABELS: Record<string, string> = {
  valor_nao_numerico: 'Valor não numérico',
  data_invalida: 'Data inválida',
  campo_obrigatorio_vazio: 'Campo obrigatório vazio',
  natureza_desconhecida: 'Natureza (débito/crédito) desconhecida',
  data_fora_da_competencia: 'Data fora da competência informada',
  campo_longo_demais: 'Campo longo demais',
};

export function lineReasonLabel(reason: string): string {
  return INVALID_LINE_REASON_LABELS[reason] ?? reason;
}

export interface InvalidLine {
  line: number;
  reason: string;
}

export type FileRefusal =
  | { code: 'SEM_MAPEAMENTO'; userMessage: string; foundColumns: string[] }
  | { code: 'SINAL_NAO_DECLARADO'; userMessage: string }
  | { code: 'FORMATO_NAO_SUPORTADO'; userMessage: string }
  | { code: 'ARQUIVO_INVALIDO'; userMessage: string }
  | { code: 'ARQUIVO_JA_PROCESSADO'; userMessage: string }
  | {
      code: 'CABECALHO_DIVERGENTE';
      userMessage: string;
      missingColumns: string[];
      foundColumns: string[];
    }
  | {
      code: 'LINHAS_INVALIDAS';
      userMessage: string;
      /** Até K linhas (o servidor recorta); `total` é quantas havia de fato. */
      lines: InvalidLine[];
      total: number;
    }
  | {
      code: 'TOTAL_DIVERGENTE';
      userMessage: string;
      /** Decimais como TEXTO (`"1234.56"`) — formatação só na exibição. */
      declaredTotal: string | null;
      computedTotal: string | null;
    };

function readStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === 'string');
}

function readString(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function readLines(value: unknown): InvalidLine[] {
  if (!Array.isArray(value)) return [];
  const lines: InvalidLine[] = [];
  for (const item of value) {
    if (typeof item !== 'object' || item === null) continue;
    const { line, reason } = item as { line?: unknown; reason?: unknown };
    if (typeof line === 'number' && typeof reason === 'string') lines.push({ line, reason });
  }
  return lines;
}

/**
 * A recusa tipada, ou `null` quando o erro é outra coisa (aí o caller degrada
 * com toast). Recebe `unknown` de propósito: é chamada sobre o `error` cru da
 * mutation.
 */
export function readFileRefusal(error: unknown): FileRefusal | null {
  if (!(error instanceof ApiError)) return null;
  const code = FILE_REFUSAL_CODES.find((candidate) => candidate === error.code);
  if (code === undefined) return null;
  const details = error.details;
  const userMessage = error.userMessage;
  switch (code) {
    case 'SEM_MAPEAMENTO':
      return { code, userMessage, foundColumns: readStringList(details.foundColumns) };
    case 'CABECALHO_DIVERGENTE':
      return {
        code,
        userMessage,
        missingColumns: readStringList(details.missingColumns),
        foundColumns: readStringList(details.foundColumns),
      };
    case 'LINHAS_INVALIDAS': {
      const lines = readLines(details.lines);
      const total = typeof details.total === 'number' ? details.total : lines.length;
      return { code, userMessage, lines, total };
    }
    case 'TOTAL_DIVERGENTE':
      return {
        code,
        userMessage,
        declaredTotal: readString(details.declaredTotal),
        computedTotal: readString(details.computedTotal),
      };
    case 'SINAL_NAO_DECLARADO':
    case 'FORMATO_NAO_SUPORTADO':
    case 'ARQUIVO_INVALIDO':
    case 'ARQUIVO_JA_PROCESSADO':
      return { code, userMessage };
  }
}

/** Açúcar: "isto é recusa do arquivo, e não falha?". */
export function isFileRefusal(error: unknown): boolean {
  return readFileRefusal(error) !== null;
}
