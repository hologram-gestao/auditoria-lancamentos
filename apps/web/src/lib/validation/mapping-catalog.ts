/**
 * Schemas Zod do catálogo de alvos do de-para (86e3n70pn).
 *
 * Espelham `MappingTargetCreate` e `MappingTargetUpdate` do backend
 * (`modules/mapping_catalog/schemas.py`): código de 1 a 50 sem espaço, nome de 1 a
 * 200 com espaços colapsados, até 500 alvos por lote. O servidor revalida tudo; a
 * tela só antecipa o erro para a linha certa.
 *
 * **Colar várias linhas.** Quem cadastra o demonstrativo copia a lista de uma
 * planilha. Cada linha é `código;nome`; o TAB (o que a planilha põe ao copiar duas
 * colunas) vale como separador também. Só o PRIMEIRO separador divide: o nome pode
 * ter `;` dentro.
 */
import { z } from 'zod';

/** `MAX_TARGET_CODE_CHARS` do servidor. */
export const MAX_TARGET_CODE_CHARS = 50;
/** `MAX_TARGET_NAME_CHARS` do servidor. */
export const MAX_TARGET_NAME_CHARS = 200;
/** `MAX_TARGETS_PER_BATCH` do servidor. */
export const MAX_TARGETS_PER_BATCH = 500;

export interface ParsedTargetLine {
  code: string;
  name: string;
}

export interface TargetLineProblem {
  /** Linha no texto colado, a partir de 1. */
  line: number;
  message: string;
}

export interface ParsedTargetLines {
  targets: ParsedTargetLine[];
  problems: TargetLineProblem[];
}

const SEPARATOR = /[;\t]/;

/**
 * Lê o texto colado: linhas em branco são ignoradas, cada uma das outras vira um
 * alvo ou um problema NOMEANDO a linha. Código repetido no próprio texto é problema
 * aqui, como seria 409 no servidor (o lote é atômico).
 */
export function parseTargetLines(text: string): ParsedTargetLines {
  const targets: ParsedTargetLine[] = [];
  const problems: TargetLineProblem[] = [];
  const seen = new Map<string, number>();
  text.split(/\r?\n/).forEach((raw, index) => {
    const line = index + 1;
    if (raw.trim() === '') return;
    const match = SEPARATOR.exec(raw);
    if (match === null) {
      problems.push({ line, message: 'use "código;nome"' });
      return;
    }
    const code = raw.slice(0, match.index).trim();
    const name = raw
      .slice(match.index + 1)
      .split(/\s+/)
      .filter(Boolean)
      .join(' ');
    if (code === '') {
      problems.push({ line, message: 'falta o código' });
    } else if (/\s/.test(code)) {
      problems.push({ line, message: 'o código não pode ter espaço' });
    } else if (code.length > MAX_TARGET_CODE_CHARS) {
      problems.push({ line, message: `código com mais de ${MAX_TARGET_CODE_CHARS} caracteres` });
    } else if (name === '') {
      problems.push({ line, message: 'falta o nome' });
    } else if (name.length > MAX_TARGET_NAME_CHARS) {
      problems.push({ line, message: `nome com mais de ${MAX_TARGET_NAME_CHARS} caracteres` });
    } else if (seen.has(code)) {
      problems.push({ line, message: `código ${code} repetido (linha ${seen.get(code)})` });
    } else {
      seen.set(code, line);
      targets.push({ code, name });
    }
  });
  return { targets, problems };
}

/** Formulário "Adicionar alvos": um campo de texto com as linhas coladas. */
export const targetLinesFormSchema = z.object({
  lines: z.string().superRefine((value, ctx) => {
    const { targets, problems } = parseTargetLines(value);
    if (problems.length > 0) {
      const shown = problems
        .slice(0, 5)
        .map((p) => `linha ${p.line}: ${p.message}`)
        .join('; ');
      const more = problems.length > 5 ? ` (e mais ${problems.length - 5})` : '';
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: `Corrija ${shown}${more}.` });
      return;
    }
    if (targets.length === 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'Cole ao menos uma linha no formato código;nome.',
      });
    } else if (targets.length > MAX_TARGETS_PER_BATCH) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: `No máximo ${MAX_TARGETS_PER_BATCH} alvos por vez.`,
      });
    }
  }),
});

export type TargetLinesFormValues = z.infer<typeof targetLinesFormSchema>;

/**
 * Formulário "Editar alvo": o NOME. O código é a chave da importação e do snapshot
 * da materialização, e não muda; a situação muda pela ação "Inativar"/"Reativar"
 * da linha, sem abrir formulário.
 */
export const targetEditFormSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, 'Informe o nome do alvo.')
    .max(MAX_TARGET_NAME_CHARS, `Nome muito longo (máx. ${MAX_TARGET_NAME_CHARS}).`),
});

export type TargetEditFormValues = z.infer<typeof targetEditFormSchema>;
