/**
 * As recusas do plano de contas CONTÁBIL, tipadas e num lugar só (Sprint 16 —
 * BACK 16.1 · 16.3).
 *
 * A importação reusa o vocabulário de recusa de arquivo da S14 (mesmos `code`),
 * mas com `details` PRÓPRIOS — o cabeçalho aqui é o MODELO da plataforma, não
 * um mapeamento do cliente, então o que importa é o que falta, o que sobra e o
 * que repete; e os motivos de linha são outros oito. Por isso um leitor
 * separado de `file-origin-errors.ts`, e não um `switch` a mais lá: a mesma
 * palavra (`CABECALHO_DIVERGENTE`) com instrução diferente para a pessoa.
 *
 * **Nenhum conteúdo de célula chega aqui** — o servidor manda nomes de coluna
 * (estrutura), número de linha e motivo fechado, e este leitor só repassa o que
 * consegue tipar (ADR-047-FE). Código fora da lista → `null`: o caller cai no
 * toast com o `userMessage`, o único caso em que isso é aceitável.
 */
import { ApiError } from '@/lib/api/client';

/** Os quatro códigos de recusa da importação (BACK 16.1), todos 422 e sem gravar nada. */
export const ACCOUNTING_CHART_REFUSAL_CODES = [
  'FORMATO_NAO_SUPORTADO',
  'ARQUIVO_INVALIDO',
  'CABECALHO_DIVERGENTE',
  'LINHAS_INVALIDAS',
] as const;

export type AccountingChartRefusalCode = (typeof ACCOUNTING_CHART_REFUSAL_CODES)[number];

/**
 * Motivos de linha inválida do leitor do plano (`client_accounting_chart/sheet.py`),
 * vocabulário FECHADO. Valor fora dele (backend mais novo) sai cru, nunca some.
 */
export const ACCOUNTING_LINE_REASON_LABELS: Record<string, string> = {
  codigo_vazio: 'Código reduzido vazio',
  codigo_longo: 'Código reduzido com mais de 20 caracteres',
  codigo_invalido: 'Código reduzido com caractere não permitido (só letras, dígitos, "." e "-")',
  codigo_repetido: 'Código reduzido repetido na planilha',
  nome_vazio: 'Nome vazio',
  nome_longo: 'Nome com mais de 200 caracteres',
  tipo_invalido: 'Tipo diferente de "analitica" ou "sintetica"',
  classificacao_longa: 'Classificação com mais de 40 caracteres',
};

export function accountingLineReasonLabel(reason: string): string {
  return ACCOUNTING_LINE_REASON_LABELS[reason] ?? reason;
}

export interface AccountingInvalidLine {
  line: number;
  reason: string;
}

export type AccountingChartRefusal =
  | { code: 'FORMATO_NAO_SUPORTADO'; userMessage: string }
  | {
      code: 'ARQUIVO_INVALIDO';
      userMessage: string;
      /** `true` quando a planilha abriu mas não tinha nenhuma conta (`reason=sem_contas`). */
      noAccounts: boolean;
    }
  | {
      code: 'CABECALHO_DIVERGENTE';
      userMessage: string;
      /** Obrigatórias ausentes — nomes do MODELO, não da planilha. */
      missingColumns: string[];
      /** Repetidas que são do modelo (`nome;nome`); o resto vira contagem. */
      repeatedColumns: string[];
      /**
       * Quantas colunas fora do modelo a planilha trazia, e quantas ao todo.
       * São CONTAGENS de propósito: planilha sem cabeçalho tem dado na linha 1,
       * e o servidor não devolve texto vindo do arquivo (§4.5).
       */
      unexpectedColumnCount: number;
      foundColumnCount: number;
    }
  | {
      code: 'LINHAS_INVALIDAS';
      userMessage: string;
      /** Até 50 linhas (o servidor recorta); `total` é quantas havia de fato. */
      lines: AccountingInvalidLine[];
      total: number;
    };

function readStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === 'string');
}

function readCount(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : 0;
}

function readLines(value: unknown): AccountingInvalidLine[] {
  if (!Array.isArray(value)) return [];
  const lines: AccountingInvalidLine[] = [];
  for (const item of value) {
    if (typeof item !== 'object' || item === null) continue;
    const { line, reason } = item as { line?: unknown; reason?: unknown };
    if (typeof line === 'number' && typeof reason === 'string') lines.push({ line, reason });
  }
  return lines;
}

/** A recusa tipada da importação, ou `null` (aí o caller degrada com toast). */
export function readAccountingChartRefusal(error: unknown): AccountingChartRefusal | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  const code = ACCOUNTING_CHART_REFUSAL_CODES.find((candidate) => candidate === error.code);
  if (code === undefined) return null;
  const { details, userMessage } = error;
  switch (code) {
    case 'FORMATO_NAO_SUPORTADO':
      return { code, userMessage };
    case 'ARQUIVO_INVALIDO':
      return { code, userMessage, noAccounts: details.reason === 'sem_contas' };
    case 'CABECALHO_DIVERGENTE':
      return {
        code,
        userMessage,
        missingColumns: readStringList(details.missingColumns),
        repeatedColumns: readStringList(details.repeatedColumns),
        unexpectedColumnCount: readCount(details.unexpectedColumnCount),
        foundColumnCount: readCount(details.foundColumnCount),
      };
    case 'LINHAS_INVALIDAS': {
      const lines = readLines(details.lines);
      const total = typeof details.total === 'number' ? details.total : lines.length;
      return { code, userMessage, lines, total };
    }
  }
}

/**
 * O 422 do validador ÚNICO de conta (BACK 16.1): sintética ou inativa não
 * recebe decisão nova nem vira conta do banco. Devolve a mensagem para o
 * CAMPO — nunca toast —, ou `null` se o erro é outro.
 */
export function readNotPostableMessage(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.code !== 'CONTA_CONTABIL_NAO_LANCAVEL') return null;
  if (error.details.reason === 'sintetica') {
    return 'Esta conta é sintética (só agrupa) e não recebe lançamento. Escolha uma conta analítica.';
  }
  if (error.details.reason === 'inativa') {
    return 'Esta conta está inativa (não veio na última planilha importada). Escolha uma conta ativa.';
  }
  return error.userMessage;
}
