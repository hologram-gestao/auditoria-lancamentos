/**
 * Testes de render/interação da tabela de tipos de anomalia (S15 FRONT 11.2).
 *
 * Cobre o caminho síncrono do componente:
 *   - estados loading / error / empty / com dados;
 *   - clique no botão de editar → chama callback do parent.
 *
 * Os fluxos que disparam modais (Toggle/Delete/Edit confirm) são integrados
 * no parent — os hooks de mutation não fazem parte deste teste (são exercitados
 * em e2e Playwright na S18).
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AnomalyTypesTable } from '@/components/features/anomaly-types/anomaly-types-table';
import type { AnomalyType } from '@/lib/api/anomaly-types';

const noop = () => undefined;

function makeRow(overrides: Partial<AnomalyType> = {}): AnomalyType {
  return {
    id: '11111111-1111-1111-1111-111111111111',
    code: 'extra_charge',
    name: 'Cobrança extra',
    description: 'Lançamento sem contrapartida.',
    severity: 'critical',
    active: true,
    ...overrides,
  };
}

describe('AnomalyTypesTable', () => {
  it('mostra estado de loading', () => {
    render(
      <AnomalyTypesTable
        rows={[]}
        isLoading
        isError={false}
        errorMessage=""
        onEdit={noop}
        onToggle={noop}
        onDelete={noop}
      />,
    );
    expect(screen.getByText(/Carregando tipos/i)).toBeInTheDocument();
  });

  it('mostra a mensagem de erro', () => {
    render(
      <AnomalyTypesTable
        rows={[]}
        isLoading={false}
        isError
        errorMessage="Boom"
        onEdit={noop}
        onToggle={noop}
        onDelete={noop}
      />,
    );
    expect(screen.getByText('Boom')).toBeInTheDocument();
  });

  it('mostra estado vazio quando rows.length === 0', () => {
    render(
      <AnomalyTypesTable
        rows={[]}
        isLoading={false}
        isError={false}
        errorMessage=""
        onEdit={noop}
        onToggle={noop}
        onDelete={noop}
      />,
    );
    expect(screen.getByText(/Nenhum tipo encontrado/i)).toBeInTheDocument();
  });

  it('renderiza nome + code + severity dos rows', () => {
    render(
      <AnomalyTypesTable
        rows={[
          makeRow(),
          makeRow({
            id: '2',
            code: 'missing_entry',
            name: 'Entrada faltante',
            severity: 'moderate',
          }),
        ]}
        isLoading={false}
        isError={false}
        errorMessage=""
        onEdit={noop}
        onToggle={noop}
        onDelete={noop}
      />,
    );
    expect(screen.getByText('Cobrança extra')).toBeInTheDocument();
    expect(screen.getByText('extra_charge')).toBeInTheDocument();
    expect(screen.getByText('Crítico')).toBeInTheDocument();
    expect(screen.getByText('Moderado')).toBeInTheDocument();
  });

  it('chama onEdit quando o botão de editar é clicado', () => {
    const onEdit = vi.fn();
    const row = makeRow();
    render(
      <AnomalyTypesTable
        rows={[row]}
        isLoading={false}
        isError={false}
        errorMessage=""
        onEdit={onEdit}
        onToggle={noop}
        onDelete={noop}
      />,
    );
    fireEvent.click(screen.getByLabelText(/Editar Cobrança extra/i));
    expect(onEdit).toHaveBeenCalledWith(row);
  });

  it('chama onToggle quando o Switch é clicado', () => {
    const onToggle = vi.fn();
    const row = makeRow();
    render(
      <AnomalyTypesTable
        rows={[row]}
        isLoading={false}
        isError={false}
        errorMessage=""
        onEdit={noop}
        onToggle={onToggle}
        onDelete={noop}
      />,
    );
    fireEvent.click(screen.getByLabelText(/Desativar Cobrança extra/i));
    expect(onToggle).toHaveBeenCalledWith(row);
  });
});
