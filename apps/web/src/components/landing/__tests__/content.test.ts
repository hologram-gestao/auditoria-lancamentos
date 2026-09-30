/**
 * Travas do texto da landing (regras da copy em `Docs/landing/COPY.md`): sem o nome
 * antigo do produto nem a sigla antiga (escritos AQUI, não derivados da constante),
 * o nome novo só pela constante, sem travessão e com a versão do consentimento igual
 * à do backend.
 */
import { readFileSync, statSync } from 'node:fs';
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

  it('o tamanho anunciado do manual é o do arquivo em public/', () => {
    const bytes = statSync(
      resolve(__dirname, '../../../../public', content.manual.href.slice(1)),
    ).size;
    const megabytes = Math.round(bytes / 1_000_000);
    expect(content.manual.size).toBe(`PDF, ${megabytes} MB`);
  });

  it('não anuncia XLS: o servidor recusa `.xls` pelos magic bytes', () => {
    expect(ALL_TEXT).not.toMatch(/\bXLS\b/i);
  });

  /**
   * Tetos de texto (86e3gwzj0, feedback do Laio: "dá para enxugar o texto"). O teste
   * falha quando alguém volta a encher um card, um par ou um passo; subir um teto é
   * decisão de copy, não ajuste de teste.
   */
  describe('tetos de texto por campo', () => {
    const cabe = (campo: string, texto: string, teto: number) =>
      expect(texto.length, `${campo} (${texto.length} caracteres): "${texto}"`).toBeLessThanOrEqual(
        teto,
      );

    it('o subtítulo do hero é curto', () => {
      cabe('hero.subtitle', content.hero.subtitle, 160);
    });

    it('cada card de "Para quem" é uma frase de até 110 caracteres', () => {
      for (const item of content.audience.items) {
        cabe(`audience "${item.title}"`, item.text, 110);
        expect(item.text.replace(/\.$/, ''), `audience "${item.title}" em uma frase`).not.toMatch(
          /[.!?]/,
        );
      }
    });

    it('cada par de "Dores e respostas": dor até 120, resposta até 160, com ícone', () => {
      for (const [indice, item] of content.pains.items.entries()) {
        cabe(`pains[${indice}].pain`, item.pain, 120);
        cabe(`pains[${indice}].answer`, item.answer, 160);
        expect(item.icon).toBeTruthy();
      }
    });

    it('cada passo de "Como funciona" tem até 110 caracteres', () => {
      for (const step of content.how.steps) cabe(`how "${step.title}"`, step.text, 110);
    });

    it('cada item de segurança tem até 120 caracteres', () => {
      for (const item of content.security.items) cabe(`security "${item.title}"`, item.text, 120);
    });

    it('cada tela do tour: frase até 120, dois bullets até 80', () => {
      for (const item of content.tour.items) {
        cabe(`tour "${item.tab}"`, item.text, 120);
        expect(item.bullets, `tour "${item.tab}"`).toHaveLength(2);
        for (const bullet of item.bullets) cabe(`tour "${item.tab}" bullet`, bullet, 80);
      }
    });
  });

  it('a versão do consentimento é a mesma do backend', () => {
    const schemas = readFileSync(
      resolve(__dirname, '../../../../../api/app/modules/leads/schemas.py'),
      'utf8',
    );
    expect(schemas).toContain(`CONSENT_TEXT_VERSION = "${content.CONSENT_TEXT_VERSION}"`);
  });
});
