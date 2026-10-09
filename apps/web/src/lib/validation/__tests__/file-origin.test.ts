/**
 * O `.xls` é SELECIONÁVEL e chega ao servidor (86e3gkd50, lido desde 86e3n70p6).
 *
 * Na demo de 29/09 o plano de contas exportado do Domínio (.xls) nem aparecia no
 * seletor: o `accept` o filtrava em silêncio. Hoje o servidor lê o `.xls`; o que
 * não é planilha (HTML salvo como `.xls`, documento corrompido) é recusado lá com
 * `FORMATO_NAO_SUPORTADO` e motivo. Estes testes travam as duas metades: o
 * `accept` mostra o `.xls`, e a validação do navegador não o barra antes do envio.
 */
import { describe, expect, it } from 'vitest';

import { FILE_ACCEPT } from '@/components/features/file-origin/file-refusal-notice';
import { accountingChartImportSchema } from '@/lib/validation/accounting-chart';
import {
  fileUploadFormSchema,
  hasSelectableExtension,
  SELECTABLE_FILE_EXTENSIONS,
} from '@/lib/validation/file-origin';

function arquivo(name: string): File {
  return new File(['conteudo'], name);
}

function fileIssues(result: { success: boolean; error?: { issues: { path: unknown[] }[] } }) {
  return result.success ? [] : (result.error?.issues ?? []).filter((i) => i.path[0] === 'file');
}

describe('FILE_ACCEPT', () => {
  const tokens = FILE_ACCEPT.split(',');

  it.each(['.csv', '.xlsx', '.xls'])('mostra %s no seletor', (ext) => {
    expect(tokens).toContain(ext);
  });

  it('declara o MIME do Excel antigo, além dos de CSV e XLSX', () => {
    expect(tokens).toEqual(
      expect.arrayContaining([
        'text/csv',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.ms-excel',
      ]),
    );
  });

  it('é o MESMO par da validação do navegador: toda extensão selecionável está no accept', () => {
    for (const ext of SELECTABLE_FILE_EXTENSIONS) expect(tokens).toContain(ext);
  });
});

describe('hasSelectableExtension', () => {
  it.each(['agosto.csv', 'agosto.xlsx', 'plano-dominio.xls', 'PLANO.XLS'])('%s passa', (name) => {
    expect(hasSelectableExtension(name)).toBe(true);
  });

  it.each(['extrato.pdf', 'planilha.ods', 'sem-extensao'])('%s não passa', (name) => {
    expect(hasSelectableExtension(name)).toBe(false);
  });
});

describe('o .xls segue para o servidor', () => {
  it('envio do arquivo do mês: nenhum erro de arquivo para .xls', () => {
    const result = fileUploadFormSchema.safeParse({
      competence: '2026-08',
      declaredTotalCents: '',
      file: arquivo('agosto.xls'),
    });
    expect(fileIssues(result)).toEqual([]);
  });

  it('envio do arquivo do mês: PDF continua barrado no navegador', () => {
    const result = fileUploadFormSchema.safeParse({
      competence: '2026-08',
      declaredTotalCents: '',
      file: arquivo('extrato.pdf'),
    });
    expect(fileIssues(result)).toHaveLength(1);
  });

  it('importar plano contábil: .xls passa, PDF não', () => {
    expect(accountingChartImportSchema.safeParse({ file: arquivo('plano.xls') }).success).toBe(
      true,
    );
    expect(accountingChartImportSchema.safeParse({ file: arquivo('plano.pdf') }).success).toBe(
      false,
    );
  });
});
