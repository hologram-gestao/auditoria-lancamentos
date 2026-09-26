/**
 * `TabsContent` esconde o painel INATIVO de verdade (validação humana da Sprint 12).
 *
 * O Radix mantém o painel inativo montado com o atributo `hidden`, e uma classe
 * de display do consumidor (`flex`, que o de-para e a carteira usam para a
 * cadeia de altura) vence o `hidden` do HTML. Com `flex-1`, o painel invisível
 * crescia e abria 209px de vão entre as abas e a prévia do de-para. A guarda
 * geométrica vive no e2e (`a11y-mocked.spec.ts`, cenário "nunca sincronizada");
 * aqui se trava a classe, porque o jsdom não aplica o CSS do Tailwind.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

describe('TabsContent', () => {
  it('leva `data-[state=inactive]:hidden` mesmo quando o consumidor pede `flex`', () => {
    render(
      <Tabs defaultValue="b">
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
          <TabsTrigger value="b">B</TabsTrigger>
        </TabsList>
        <TabsContent value="a" className="flex min-h-0 flex-1 flex-col" data-testid="painel-a">
          conteúdo A
        </TabsContent>
        <TabsContent value="b" data-testid="painel-b">
          conteúdo B
        </TabsContent>
      </Tabs>,
    );
    const inativo = screen.getByTestId('painel-a');
    expect(inativo).toHaveAttribute('data-state', 'inactive');
    expect(inativo).toHaveAttribute('hidden');
    expect(inativo).toHaveClass('data-[state=inactive]:hidden', 'flex');
    expect(screen.getByTestId('painel-b')).toHaveAttribute('data-state', 'active');
    expect(screen.getByTestId('painel-b')).not.toHaveAttribute('hidden');
  });
});
