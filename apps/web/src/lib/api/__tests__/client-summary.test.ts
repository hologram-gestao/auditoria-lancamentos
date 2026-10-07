/**
 * Caminho do resumo do cliente (86e3k1q3j): sem mês, nenhuma query (o servidor
 * decide o mês corrente); com mês, `?month=YYYY-MM`.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiGet = vi.fn();

vi.mock('@/lib/api/client', () => ({
  apiGet: (path: string) => apiGet(path) as unknown,
}));

import { getClientSummary } from '@/lib/api/client-summary';

beforeEach(() => {
  apiGet.mockReset();
  apiGet.mockResolvedValue({});
});

describe('getClientSummary', () => {
  it('sem mês, não manda query: o mês corrente é do servidor', async () => {
    await getClientSummary('c1');
    expect(apiGet).toHaveBeenCalledWith('/api/v1/clients/c1/summary');
  });

  it('com mês, manda ?month=YYYY-MM', async () => {
    await getClientSummary('c1', '2026-09');
    expect(apiGet).toHaveBeenCalledWith('/api/v1/clients/c1/summary?month=2026-09');
  });
});
