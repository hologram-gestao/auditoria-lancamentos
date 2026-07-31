'use client';

/**
 * AlertDialog — confirmação de AÇÃO DESTRUTIVA (design-system).
 *
 * O que o diferencia de um `Dialog` comum, e por que isso importa:
 *   - `role="alertdialog"`: o leitor de tela anuncia como alerta e lê a
 *     descrição junto do título; `dialog` comum não dá essa urgência.
 *   - **Não fecha por clique fora nem por foco fora.** Uma confirmação
 *     destrutiva tem duas saídas explícitas: Cancelar ou Confirmar. `Esc`
 *     continua fechando (é o "cancelar" do teclado, e removê-lo prende o foco).
 *   - **Sem "X" no canto**: um terceiro caminho de saída ambíguo.
 *   - O foco inicial vai para **Cancelar**, não para a ação destrutiva —
 *     `Enter` reflexo não deve apagar nada.
 *
 * **Por que sobre `@radix-ui/react-dialog` e não `@radix-ui/react-alert-dialog`:**
 * a semântica de alertdialog é obtida integralmente aqui (o Radix aplica
 * `...contentProps` DEPOIS do seu `role: "dialog"`, então o override vale), e
 * instalar um pacote novo mexeria no `pnpm-lock.yaml` da RAIZ — fora do escopo
 * de escrita deste agent (`apps/web/`), o que deixaria o lockfile fora do
 * commit e quebraria o `pnpm install --frozen-lockfile` do CI (foi exatamente o
 * vermelho da Sprint 4). Isto não é `<div fixed inset-0>` manual: foco preso,
 * `aria-modal`, restauração de foco e portal continuam sendo do Radix.
 */

import * as DialogPrimitive from '@radix-ui/react-dialog';
import * as React from 'react';

import { Button, type ButtonProps } from '@/components/ui/button';
import { cn } from '@/lib/utils';

const AlertDialog = DialogPrimitive.Root;
const AlertDialogTrigger = DialogPrimitive.Trigger;
const AlertDialogPortal = DialogPrimitive.Portal;

/**
 * O `AlertDialogContent` precisa focar o Cancelar na abertura, mas quem o
 * renderiza é o caller. Um contexto com uma ref resolve isso sem `querySelector`
 * (que dependeria de um `data-*` que o tipo do `Button` não aceita) e sem
 * exigir que cada tela passe a ref à mão.
 */
const CancelRefContext = React.createContext<React.MutableRefObject<HTMLButtonElement | null> | null>(
  null,
);

function mergeRefs<T>(...refs: (React.Ref<T> | undefined)[]): React.RefCallback<T> {
  return (value) => {
    for (const ref of refs) {
      if (typeof ref === 'function') ref(value);
      else if (ref) (ref as React.MutableRefObject<T | null>).current = value;
    }
  };
}

const AlertDialogOverlay = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn(
      'data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 fixed inset-0 z-50 bg-black/80',
      className,
    )}
    {...props}
  />
));
AlertDialogOverlay.displayName = 'AlertDialogOverlay';

const AlertDialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>
>(({ className, onOpenAutoFocus, ...props }, ref) => {
  const cancelRef = React.useRef<HTMLButtonElement | null>(null);
  return (
    <AlertDialogPortal>
      <AlertDialogOverlay />
      <CancelRefContext.Provider value={cancelRef}>
        <DialogPrimitive.Content
          ref={ref}
          role="alertdialog"
          onPointerDownOutside={(event) => event.preventDefault()}
          onInteractOutside={(event) => event.preventDefault()}
          onOpenAutoFocus={(event) => {
            onOpenAutoFocus?.(event);
            if (event.defaultPrevented) return;
            if (cancelRef.current) {
              event.preventDefault();
              cancelRef.current.focus();
            }
          }}
          className={cn(
            'bg-background data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 fixed left-[50%] top-[50%] z-50 grid w-full max-w-lg translate-x-[-50%] translate-y-[-50%] gap-4 border p-6 shadow-lg duration-200 sm:rounded-lg',
            className,
          )}
          {...props}
        />
      </CancelRefContext.Provider>
    </AlertDialogPortal>
  );
});
AlertDialogContent.displayName = 'AlertDialogContent';

const AlertDialogHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn('flex flex-col space-y-1.5 text-left', className)} {...props} />
);
AlertDialogHeader.displayName = 'AlertDialogHeader';

/**
 * Cancelar à ESQUERDA, ação destrutiva à DIREITA (`justify-between`) — o mesmo
 * contrato do rodapé da gaveta, para o gesto não mudar de lugar entre telas.
 */
const AlertDialogFooter = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn('flex items-center justify-between gap-2', className)} {...props} />
);
AlertDialogFooter.displayName = 'AlertDialogFooter';

const AlertDialogTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn('text-foreground text-lg font-semibold', className)}
    {...props}
  />
));
AlertDialogTitle.displayName = 'AlertDialogTitle';

/**
 * Obrigatória: é ela que o `aria-describedby` do alertdialog aponta. Sem
 * descrição, o Radix ainda emite `aria-describedby` para um id inexistente e o
 * axe acusa `aria-valid-attr-value`.
 */
const AlertDialogDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn('text-muted-foreground text-sm', className)}
    {...props}
  />
));
AlertDialogDescription.displayName = 'AlertDialogDescription';

const AlertDialogCancel = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'outline', ...props }, ref) => {
    const cancelRef = React.useContext(CancelRefContext);
    return (
      <DialogPrimitive.Close asChild>
        <Button ref={mergeRefs(ref, cancelRef)} type="button" variant={variant} {...props} />
      </DialogPrimitive.Close>
    );
  },
);
AlertDialogCancel.displayName = 'AlertDialogCancel';

/**
 * A ação destrutiva NÃO é um `Dialog.Close`: quem fecha é o caller, depois que
 * a mutation resolve — senão a gaveta some antes do spinner e o erro do
 * servidor não teria onde aparecer.
 */
const AlertDialogAction = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'destructive', ...props }, ref) => (
    <Button ref={ref} type="button" variant={variant} {...props} />
  ),
);
AlertDialogAction.displayName = 'AlertDialogAction';

export {
  AlertDialog,
  AlertDialogTrigger,
  AlertDialogPortal,
  AlertDialogOverlay,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogCancel,
  AlertDialogAction,
};
