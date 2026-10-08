import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AnimatedCheck } from '../animated-check';

describe('AnimatedCheck (86e3h579d)', () => {
  it('é decorativo, pinta por currentColor e leva a classe do desenho', () => {
    const { container } = render(<AnimatedCheck className="text-success h-14 w-14" />);
    const svg = container.querySelector('svg');
    expect(svg).not.toBeNull();
    expect(svg).toHaveAttribute('aria-hidden', 'true');
    expect(svg).toHaveAttribute('stroke', 'currentColor');
    expect(svg).toHaveAttribute('fill', 'none');
    expect(svg).toHaveClass('animated-check', 'text-success', 'h-14', 'w-14');
  });

  it('mantém a geometria da landing (círculo de raio 25 e o traço) e o traço de 3', () => {
    const { container } = render(<AnimatedCheck />);
    expect(container.querySelector('svg')).toHaveAttribute('viewBox', '0 0 56 56');
    expect(container.querySelector('svg')).toHaveAttribute('stroke-width', '3');
    expect(container.querySelector('circle')).toHaveAttribute('r', '25');
    expect(container.querySelector('path')).toHaveAttribute('d', 'M17 29l7 7 15-16');
  });

  it('o ícone pequeno (toast) engrossa o traço por prop', () => {
    const { container } = render(<AnimatedCheck strokeWidth={4.5} />);
    expect(container.querySelector('svg')).toHaveAttribute('stroke-width', '4.5');
  });
});
