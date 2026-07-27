/**
 * Testes da camada ÚNICA de formatação (`lib/format.ts`).
 *
 * O foco é o que já mordeu o projeto antes: `new Date('2026-06-01')` é UTC e,
 * em fuso a oeste de Greenwich, "Junho de 2026" vira "Maio de 2026". Todos os
 * formatadores de data aqui fazem parse manual — estes testes travam isso.
 */
import { describe, expect, it, vi, afterEach } from 'vitest';

import {
  formatOmieAccountType,
  formatReferenceMonth,
  formatSyncedAt,
  toMonthInputValue,
} from '@/lib/format';

describe('formatReferenceMonth', () => {
  it('formata o 1º dia do mês sem timezone-shift', () => {
    expect(formatReferenceMonth('2026-06-01')).toBe('Junho de 2026');
    // 1º de janeiro é o caso extremo: um shift de -3h joga para dezembro/ano anterior.
    expect(formatReferenceMonth('2026-01-01')).toBe('Janeiro de 2026');
  });

  it('aceita também o formato YYYY-MM do <input type="month">', () => {
    expect(formatReferenceMonth('2026-12')).toBe('Dezembro de 2026');
  });

  it('degrada sem quebrar em entrada ausente ou fora do padrão', () => {
    expect(formatReferenceMonth(null)).toBe('—');
    expect(formatReferenceMonth(undefined)).toBe('—');
    expect(formatReferenceMonth('mês que vem')).toBe('mês que vem');
    // Mês 13 não existe: devolve a entrada em vez de "undefined de 2026".
    expect(formatReferenceMonth('2026-13-01')).toBe('2026-13-01');
  });
});

describe('toMonthInputValue', () => {
  it('converte YYYY-MM-DD para o valor do month picker', () => {
    expect(toMonthInputValue('2026-06-01')).toBe('2026-06');
  });
});

describe('formatOmieAccountType', () => {
  it('mapeia os códigos comuns do Omie', () => {
    expect(formatOmieAccountType('CC')).toBe('Conta Corrente');
    expect(formatOmieAccountType('CR')).toBe('Cartão de Crédito');
    // Regressão do bug M-1: CA é APLICAÇÃO, nunca cartão.
    expect(formatOmieAccountType('CA')).toBe('Conta Aplicação');
  });

  it('normaliza espaço/caixa que o Omie às vezes devolve', () => {
    expect(formatOmieAccountType(' cr ')).toBe('Cartão de Crédito');
  });

  it('devolve o código cru em tipo desconhecido (não quebra a UI)', () => {
    expect(formatOmieAccountType('ZZ')).toBe('ZZ');
  });
});

describe('formatSyncedAt', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('diz "agora" abaixo de 1 minuto', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-06-10T12:00:30Z'));
    expect(formatSyncedAt('2026-06-10T12:00:00Z')).toBe('Sincronizado agora');
  });

  it('escala para minutos, horas e dias', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-06-10T12:00:00Z'));
    expect(formatSyncedAt('2026-06-10T11:30:00Z')).toBe('Sincronizado há 30 min');
    expect(formatSyncedAt('2026-06-10T09:00:00Z')).toBe('Sincronizado há 3 h');
    expect(formatSyncedAt('2026-06-09T12:00:00Z')).toBe('Sincronizado há 1 dia');
    expect(formatSyncedAt('2026-06-05T12:00:00Z')).toBe('Sincronizado há 5 dias');
  });

  it('trata ausência e valor inválido como "nunca sincronizado"', () => {
    expect(formatSyncedAt(null)).toBe('Nunca sincronizado');
    expect(formatSyncedAt(undefined)).toBe('Nunca sincronizado');
    expect(formatSyncedAt('não é data')).toBe('Nunca sincronizado');
  });
});
