/**
 * Trava a marca num lugar só (86e3fr9x3): o nome do produto não pode voltar a ser
 * escrito à mão em tela nenhuma, senão a troca pelo nome novo deixa de ser uma
 * linha em `lib/brand.ts`.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';

import { describe, expect, it } from 'vitest';

import { PRODUCT_NAME, PRODUCT_SHORT_NAME, PRODUCT_TITLE } from '../brand';

// `__dirname`, não `import.meta.url`: no ambiente jsdom a URL do módulo não é `file:`.
const SRC = resolve(__dirname, '..', '..');

/** Podem conter o nome: a marca, o contrato gerado pela API e este teste. */
const ALLOWED = new Set(['lib/brand.ts', 'lib/contracts/schema.ts', 'lib/__tests__/brand.test.ts']);

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.(ts|tsx)$/.test(name) ? [full] : [];
  });
}

function rel(file: string): string {
  return relative(SRC, file).split(sep).join('/');
}

const FILES = sourceFiles(SRC).filter((file) => !ALLOWED.has(rel(file)));

/** Linhas de código, sem comentários de linha nem de bloco JSDoc. */
function codeLines(file: string): string[] {
  return readFileSync(file, 'utf8')
    .split('\n')
    .filter((line) => {
      const trimmed = line.trim();
      return !(
        trimmed.startsWith('//') ||
        trimmed.startsWith('*') ||
        trimmed.startsWith('/*') ||
        trimmed.startsWith('{/*')
      );
    });
}

describe('lib/brand', () => {
  it('mantém o nome de hoje, sem mudança visual até o nome novo existir', () => {
    expect(PRODUCT_NAME).toBe('Auditoria de Lançamentos');
    expect(PRODUCT_TITLE).toBe('Sistema de Auditoria de Lançamentos');
    expect(PRODUCT_SHORT_NAME).toBe('ADL');
  });

  it('o nome do produto só aparece escrito em lib/brand.ts', () => {
    const offenders = FILES.filter((file) => readFileSync(file, 'utf8').includes(PRODUCT_NAME));
    expect(offenders.map(rel)).toEqual([]);
  });

  it('a sigla não aparece escrita à mão em texto de tela (string ou JSX)', () => {
    // Em comentário a sigla segue permitida: é assim que o time chama o sistema. Código
    // de erro do backend (`ADL-PARSE-LIMIT`) também: é identificador, não nome.
    const inString = /['"`][^'"`]*\bADL\b(?!-)[^'"`]*['"`]/;
    const inJsxText = />[^<>{}]*\bADL\b(?!-)[^<>{}]*</;
    const offenders = FILES.filter((file) =>
      codeLines(file).some((line) => inString.test(line) || inJsxText.test(line)),
    );
    expect(offenders.map(rel)).toEqual([]);
  });
});
