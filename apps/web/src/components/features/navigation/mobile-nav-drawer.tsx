'use client';

/**
 * Menu de navegação MOBILE (86e2n4pf9) — hambúrguer no header + drawer.
 *
 * Abaixo de `md` o `<aside>` do shell não existe; este drawer é O caminho de
 * navegação, renderizando o MESMO `SidebarNav` em camadas do desktop — árvore
 * única (`nav-items`), mesmo gating (§4.9). Escrever a árvore de novo aqui é
 * a armadilha registrada no épico 86e2n4tbx.
 *
 * Fecha ao navegar por DOIS caminhos que se completam:
 *   - `onNavigate` no clique do link — resposta imediata, e cobre o clique no
 *     item da página ATUAL (o pathname não muda, o efeito abaixo não dispara);
 *   - efeito no `pathname` — garantia para navegação que aconteça com o drawer
 *     aberto sem passar por um link dele.
 *
 * O Radix (Dialog por baixo do Sheet) entrega foco preso, `Esc` e a devolução
 * do foco ao botão de origem — validado no e2e, não presumido.
 */
import { Menu } from 'lucide-react';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet';
import type { AuthenticatedUser } from '@/lib/contracts';

import { SidebarNav } from './sidebar-nav';

interface MobileNavDrawerProps {
  user: AuthenticatedUser;
}

export function MobileNavDrawer({ user }: MobileNavDrawerProps) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        {/* `shrink-0`: o título ao lado trunca (`min-w-0`) — sem isso o flex
            poderia espremer o botão. Só existe abaixo de `md`, onde o aside
            não é renderizado. */}
        <Button
          variant="ghost"
          size="icon"
          className="shrink-0 md:hidden"
          aria-label="Abrir menu de navegação"
        >
          <Menu className="h-5 w-5" aria-hidden="true" />
        </Button>
      </SheetTrigger>
      {/* `aria-describedby={undefined}`: menu não tem descrição — sem isso o
          Radix avisa no console sobre o Description ausente. */}
      <SheetContent side="left" className="w-72" aria-describedby={undefined}>
        <SheetHeader className="px-4">
          <SheetTitle className="text-base">Menu</SheetTitle>
        </SheetHeader>
        <SheetBody className="px-3 py-3">
          <SidebarNav user={user} onNavigate={() => setOpen(false)} />
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}
