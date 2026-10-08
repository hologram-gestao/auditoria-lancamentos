import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Card, CardContent, CardHeader, CardTitle } from '../card';

describe('Card (86e3h579d)', () => {
  it('default é a moldura lisa, sem o efeito do elevado', () => {
    render(<Card data-testid="card">conteúdo</Card>);
    const card = screen.getByTestId('card');
    expect(card.tagName).toBe('DIV');
    expect(card).toHaveClass('bg-card', 'text-card-foreground', 'rounded-lg', 'border');
    expect(card).not.toHaveClass('card-elevated');
  });

  it('elevated aplica a classe do efeito (borda em gradiente e hover do globals.css)', () => {
    render(
      <Card data-testid="card" variant="elevated">
        conteúdo
      </Card>,
    );
    expect(screen.getByTestId('card')).toHaveClass('card-elevated', 'bg-card', 'border');
  });

  it('a classe de quem chama vence a do primitivo (rounded-xl no lugar de rounded-lg)', () => {
    render(
      <Card data-testid="card" variant="elevated" className="rounded-xl p-6">
        conteúdo
      </Card>,
    );
    const card = screen.getByTestId('card');
    expect(card).toHaveClass('rounded-xl', 'p-6');
    expect(card).not.toHaveClass('rounded-lg');
  });

  it('asChild veste o filho (o <li> da landing) sem criar elemento a mais', () => {
    render(
      <ul>
        <Card asChild variant="elevated" data-reveal style={{ color: 'red' }}>
          <li>item</li>
        </Card>
      </ul>,
    );
    const item = screen.getByRole('listitem');
    expect(item).toHaveClass('card-elevated', 'bg-card');
    expect(item).toHaveAttribute('data-reveal');
    expect(item.parentElement?.tagName).toBe('UL');
  });

  it('cabeçalho, título e conteúdo são finos e repassam a classe', () => {
    render(
      <Card>
        <CardHeader data-testid="header">
          <CardTitle>Título</CardTitle>
        </CardHeader>
        <CardContent data-testid="content" className="pt-2">
          corpo
        </CardContent>
      </Card>,
    );
    expect(screen.getByRole('heading', { level: 3, name: 'Título' })).toBeInTheDocument();
    expect(screen.getByTestId('content')).toHaveClass('p-4', 'pt-2');
    expect(screen.getByTestId('content')).not.toHaveClass('pt-0');
  });
});
