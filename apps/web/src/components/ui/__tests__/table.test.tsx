/**
 * Trava do defeito 86e2gwuxn (`scrollable-region-focusable`, SERIOUS).
 *
 * O wrapper `div.overflow-auto` do `<Table>` não tinha `tabIndex`. Em viewport
 * de 390px a tabela transborda, o div vira uma REGIÃO ROLÁVEL e o conteúdo à
 * direita só é alcançável arrastando — sem nenhuma forma de chegar nele pelo
 * teclado (WCAG 2.1.1 + 2.1.3). Reprovava o gate de a11y nas telas novas da
 * Sprint 4 (Contas Bancárias e Detalhe da conciliação) só no viewport mobile.
 *
 * **Por que a asserção é sobre o ATRIBUTO e não sobre o axe:** jsdom não tem
 * layout, então nada transborda e a regra `scrollable-region-focusable` nunca
 * dispara aqui — foi exatamente por isso que o `pnpm test` deixou o defeito
 * passar. A checagem no browser real continua sendo a do Playwright
 * (`e2e/a11y.spec.ts`, projeto `mobile`); esta trava a causa-raiz na camada de
 * baixo, que é onde o atributo pode sumir de novo num refactor do shadcn.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  Table,
  TableBody,
  TableCard,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { assertNoA11yViolations } from '@/test/a11y';

function renderTable(props?: { scrollRegionLabel?: string; fill?: boolean }) {
  return render(
    <Table {...props}>
      <TableHeader>
        <TableRow>
          <TableHead>Conta</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow>
          <TableCell>Cartão Itaú</TableCell>
        </TableRow>
      </TableBody>
    </Table>,
  );
}

describe('Table', () => {
  it('expõe o wrapper rolável ao teclado, com papel e nome', () => {
    renderTable();

    const region = screen.getByRole('region', { name: 'Tabela (rolável horizontalmente)' });
    expect(region).toHaveAttribute('tabindex', '0');
    // A tabela precisa estar DENTRO da região — é o conteúdo que transborda.
    expect(region).toContainElement(screen.getByRole('table'));
  });

  it('aceita um nome próprio para a região (telas com mais de uma tabela)', () => {
    renderTable({ scrollRegionLabel: 'Movimentações' });

    expect(screen.getByRole('region', { name: 'Movimentações' })).toBeInTheDocument();
  });

  it('não introduz violação de a11y', async () => {
    const { container } = renderTable();

    await assertNoA11yViolations(container);
  });
});

/**
 * Trava do defeito 86e2uca1d.
 *
 * jsdom não tem layout — o que se trava aqui é o CONTRATO que produz a rolagem
 * vertical, não a rolagem em si (essa é medida no browser, em
 * `e2e/a11y-mocked.spec.ts`). O par importa: sem `<TableCard>` limitando a
 * altura, o `fill` não tem contra o que encolher; sem `fill`, o card limita a
 * altura e a tabela é RECORTADA em vez de rolar.
 */
describe('Table — fill + TableCard', () => {
  it('sem `fill`, a região não vira scroller vertical nem gruda o cabeçalho', () => {
    renderTable();

    const region = screen.getByRole('region', { name: 'Tabela (rolável horizontalmente)' });
    // É o que garante que os consumidores de altura livre (abas da revisão,
    // modal de troca, listas globais) não mudam de comportamento.
    expect(region.className).not.toMatch(/min-h-0/);
    expect(region.className).not.toMatch(/sticky/);
  });

  it('com `fill`, a região encolhe até caber e o cabeçalho gruda no topo', () => {
    renderTable({ fill: true });

    const region = screen.getByRole('region', { name: 'Tabela (rolável horizontalmente)' });
    expect(region).toHaveClass('overflow-auto', 'min-h-0');
    expect(region.className).toMatch(/\[&_thead_th\]:sticky/);
    expect(region.className).toMatch(/\[&_thead_th\]:top-0/);
    // Cabeçalho grudado precisa de fundo OPACO, senão as linhas passam por trás.
    expect(region.className).toMatch(/\[&_thead_th\]:bg-background/);
  });

  it('TableCard limita a altura sem esticar quando há poucas linhas', () => {
    render(
      <TableCard data-testid="card">
        <span>conteúdo</span>
      </TableCard>,
    );

    const card = screen.getByTestId('card');
    // `max-h-full` e NÃO `h-full`: com duas linhas o card abraça o conteúdo em
    // vez de deixar uma moldura vazia e alta.
    expect(card).toHaveClass('max-h-full', 'flex', 'flex-col', 'overflow-hidden');
    expect(card.className).not.toMatch(/(^|\s)h-full(\s|$)/);
  });
});
