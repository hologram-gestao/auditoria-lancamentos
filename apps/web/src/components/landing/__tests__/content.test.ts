/**
 * Travas do texto da landing (regras da copy em `Docs/landing/COPY.md`): sem o nome
 * antigo do produto nem a sigla antiga (escritos AQUI, não derivados da constante),
 * o nome novo só pela constante, sem travessão e com a versão do consentimento igual
 * à do backend.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { PRODUCT_NAME } from '@/lib/brand';

import * as content from '../content';

const ALL_TEXT = JSON.stringify(content);

describe('landing/content', () => {
  it('não usa o nome antigo do produto nem a sigla antiga', () => {
    expect(ALL_TEXT).not.toContain('Auditoria de Lançamentos');
    expect(ALL_TEXT).not.toMatch(/\bADL\b/);
  });

  it('nomeia o produto, e sempre pela constante', () => {
    expect(ALL_TEXT).toContain(PRODUCT_NAME);
    const source = readFileSync(resolve(__dirname, '../content.ts'), 'utf8');
    expect(source).not.toContain(PRODUCT_NAME);
  });

  it('não usa travessão', () => {
    expect(ALL_TEXT).not.toContain('—');
  });

  it('não promete o que a copy proíbe', () => {
    for (const forbidden of ['garantimos', 'revolucion', '100%', 'inteligente']) {
      expect(ALL_TEXT.toLowerCase()).not.toContain(forbidden);
    }
  });

  it('a versão do consentimento é a mesma do backend', () => {
    const schemas = readFileSync(
      resolve(__dirname, '../../../../../api/app/modules/leads/schemas.py'),
      'utf8',
    );
    expect(schemas).toContain(`CONSENT_TEXT_VERSION = "${content.CONSENT_TEXT_VERSION}"`);
  });
});
