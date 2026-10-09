/**
 * O texto do "Modelo da planilha" (86e3n70p9): o cabeçalho casa por GRAFIA
 * normalizada, nunca por sinônimo, e o `tipo` aceita `analitico`/`sintetico` e
 * as iniciais `A`/`S`. É a transcrição do que `sheet.py` passou a aceitar: se o
 * leitor mudar, a doc, este componente e este teste mudam juntos.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AccountingChartModel } from '@/components/features/accounting-chart/accounting-chart-model';

describe('AccountingChartModel', () => {
  it('diz que o cabeçalho casa por grafia (não por sinônimo) e que S/A valem como tipo', () => {
    render(<AccountingChartModel headingId="modelo" />);
    const section = screen.getByRole('region', { name: 'Modelo da planilha' });
    expect(section).toHaveTextContent(
      /Maiúsculas, acentos, espaços e hífens no nome da coluna não importam/,
    );
    expect(section).toHaveTextContent(/"Código Reduzido" vale como codigo_reduzido/);
    expect(section).toHaveTextContent(/outros nomes não são aceitos/);
    expect(section).toHaveTextContent(/"analitico"\/"sintetico" ou só a inicial, "A"\/"S"/);
    // A classificação ordena como texto (a ordem do Domínio): o texto ensina a largura fixa.
    expect(section).toHaveTextContent(/A lista segue a ordem dela como texto, como no Domínio/);
    expect(section).toHaveTextContent(/use a mesma largura em cada nível \(01, 02… 10\)/);
    expect(section).toHaveTextContent(/senão 1\.1\.10 vem antes de 1\.1\.2/);
    // As quatro colunas do modelo continuam nomeadas pela grafia canônica.
    for (const column of ['codigo_reduzido', 'nome', 'tipo', 'classificacao']) {
      expect(screen.getAllByText(column, { exact: true }).length).toBeGreaterThan(0);
    }
  });
});
