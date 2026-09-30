/**
 * Trava a marca num lugar só (86e3fr9x3): o produto é o Hologram OS desde 30/09/2026.
 * O nome ANTIGO não pode voltar em arquivo nenhum, e o nome novo não pode ser escrito
 * à mão fora de `lib/brand.ts`, senão a próxima troca deixa de ser uma linha.
 *
 * Os literais antigos estão escritos AQUI, e não derivados da constante: derivados,
 * o teste passaria a procurar o nome novo e deixaria o antigo voltar calado.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';

import { describe, expect, it } from 'vitest';

import {
  COMPANY_NAME,
  PRODUCT_DOMAIN,
  PRODUCT_NAME,
  PRODUCT_SHORT_NAME,
  PRODUCT_TITLE,
} from '../brand';

const OLD_NAME = 'Auditoria de Lançamentos';

// `__dirname`, não `import.meta.url`: no ambiente jsdom a URL do módulo não é `file:`.
const SRC = resolve(__dirname, '..', '..');

/**
 * Podem conter o nome: a marca, o contrato gerado pela API e os dois testes que
 * escrevem o literal antigo para proibi-lo (este e o do texto da landing).
 */
const ALLOWED = new Set([
  'lib/brand.ts',
  'lib/contracts/schema.ts',
  'lib/__tests__/brand.test.ts',
  'components/landing/__tests__/content.test.ts',
]);

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
  it('o produto é o Hologram OS, da Hologram Gestão', () => {
    expect(PRODUCT_NAME).toBe('Hologram OS');
    expect(PRODUCT_TITLE).toBe('Hologram OS');
    expect(PRODUCT_SHORT_NAME).toBe('Hologram OS');
    expect(PRODUCT_DOMAIN).toBe('hologramos.com.br');
    expect(COMPANY_NAME).toBe('Hologram Gestão');
  });

  it('o nome antigo não aparece em arquivo nenhum', () => {
    const offenders = FILES.filter((file) => readFileSync(file, 'utf8').includes(OLD_NAME));
    expect(offenders.map(rel)).toEqual([]);
  });

  it('o nome novo só é escrito à mão em lib/brand.ts (em comentário pode)', () => {
    const offenders = FILES.filter((file) =>
      codeLines(file).some((line) => line.includes(PRODUCT_NAME)),
    );
    expect(offenders.map(rel)).toEqual([]);
  });

  it('a sigla antiga não aparece em texto de tela (string ou JSX)', () => {
    // Em comentário a sigla segue permitida: é assim que o time chamava o sistema.
    // Código de erro do backend (`ADL-PARSE-LIMIT`) também: é identificador, não nome.
    const inString = /['"`][^'"`]*\bADL\b(?!-)[^'"`]*['"`]/;
    const inJsxText = />[^<>{}]*\bADL\b(?!-)[^<>{}]*</;
    const offenders = FILES.filter((file) =>
      codeLines(file).some((line) => inString.test(line) || inJsxText.test(line)),
    );
    expect(offenders.map(rel)).toEqual([]);
  });
});
