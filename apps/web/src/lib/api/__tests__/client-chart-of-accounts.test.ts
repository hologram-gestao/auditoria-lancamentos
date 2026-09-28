/**
 * Query string da lista do plano de contas (86e3f55bc).
 *
 * `hasDreCode=false` é um RECORTE ("sem destino declarado"), não "sem filtro":
 * um `if (params.hasDreCode)` truthy o descartaria em silêncio, e o card "Sem
 * destino declarado" mostraria a lista inteira. O e2e não pega isso (o mock da
 * rota ignora a query), por isso a trava fica aqui.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiGet = vi.fn();

vi.mock('@/lib/api/client', () => ({
  apiGet: (path: string) => apiGet(path) as unknown,
  apiPost: vi.fn(),
}));

import { listChartOfAccounts } from '@/lib/api/client-chart-of-accounts';

function lastQuery(): URLSearchParams {
  const path = String(apiGet.mock.calls.at(-1)?.[0]);
  return new URLSearchParams(path.split('?')[1]);
}

beforeEach(() => {
  apiGet.mockReset();
  apiGet.mockResolvedValue({ data: [], pagination: {} });
});

describe('listChartOfAccounts', () => {
  it('manda `false` como recorte, não como ausência', async () => {
    await listChartOfAccounts('c1', { hasDreCode: false, hasAccountingCode: false });
    expect(lastQuery().get('hasDreCode')).toBe('false');
    expect(lastQuery().get('hasAccountingCode')).toBe('false');
  });

  it('manda `true` e omite o que é nulo', async () => {
    await listChartOfAccounts('c1', { hasDreCode: true, hasAccountingCode: null });
    expect(lastQuery().get('hasDreCode')).toBe('true');
    expect(lastQuery().has('hasAccountingCode')).toBe(false);
  });

  it('page e pageSize vão sempre, mesmo sem nada mais', async () => {
    await listChartOfAccounts('c1', { page: 2, pageSize: 50 });
    expect(lastQuery().get('page')).toBe('2');
    expect(lastQuery().get('pageSize')).toBe('50');
    expect(lastQuery().has('hasDreCode')).toBe(false);
  });
});
