/**
 * Tabela do catálogo de categorias (86e34jd8m): estados, contagem de clientes
 * e callbacks de editar/excluir com nome acessível por linha.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ClientCategoriesTable } from '@/components/features/client-categories/client-categories-table';
import type { ClientCategoryItem } from '@/lib/api/client-categories';
import { assertNoA11yViolations } from '@/test/a11y';

const noop = () => undefined;

function row(over: Partial<ClientCategoryItem> = {}): ClientCategoryItem {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    name: 'Fintech',
    tone: 'info',
    clients_count: 3,
    // Toda categoria pertence a uma organização desde a 86e36ecqz.
    organization_id: '0706eeb5-9718-4d03-bcda-ef615789e6ac',
    organization_name: 'Hologram',
    ...over,
  };
}

describe('ClientCategoriesTable', () => {
  it('estados de loading, erro e vazio', () => {
    const { rerender } = render(
      <ClientCategoriesTable
        rows={[]}
        isLoading
        isError={false}
        errorMessage=""
        onEdit={noop}
        onDelete={noop}
      />,
    );
    expect(screen.getByText(/Carregando categorias/)).toBeInTheDocument();

    rerender(
      <ClientCategoriesTable
        rows={[]}
        isLoading={false}
        isError
        errorMessage="Boom"
        onEdit={noop}
        onDelete={noop}
      />,
    );
    expect(screen.getByText('Boom')).toBeInTheDocument();

    rerender(
      <ClientCategoriesTable
        rows={[]}
        isLoading={false}
        isError={false}
        errorMessage=""
        onEdit={noop}
        onDelete={noop}
      />,
    );
    expect(screen.getByText(/Nenhuma categoria cadastrada/)).toBeInTheDocument();
  });

  it('linha mostra chip, contagem e dispara editar/excluir', async () => {
    const onEdit = vi.fn();
    const onDelete = vi.fn();
    const item = row();
    const { container } = render(
      <ClientCategoriesTable
        rows={[
          item,
          row({
            id: '22222222-2222-4222-8222-222222222222',
            name: 'Varejo',
            tone: 'success',
            clients_count: 0,
          }),
        ]}
        isLoading={false}
        isError={false}
        errorMessage=""
        onEdit={onEdit}
        onDelete={onDelete}
      />,
    );
    expect(screen.getByText('Fintech')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Editar Fintech' }));
    expect(onEdit).toHaveBeenCalledWith(item);
    fireEvent.click(screen.getByRole('button', { name: 'Excluir Varejo' }));
    expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ name: 'Varejo' }));

    await assertNoA11yViolations(container);
  });
});
