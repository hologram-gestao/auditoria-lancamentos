'use client';

/**
 * ENCERRAMENTO com retenção (86e36pm1z) — direção do Lucas/Pedro (09/09/2026):
 * apaga quem o cliente É (nome anonimizado, credenciais e chave de criptografia
 * destruídas, usuários do cliente anonimizados) e MANTÉM o que aconteceu
 * (conciliações, valores, datas) como histórico só-leitura.
 *
 * É terminal como a exclusão — se o cliente voltar, é cadastro novo — então
 * passa pelo mesmo ritual do `DeleteClientDialog`: `AlertDialog` (ADR-006-FE)
 * + digitar o nome do cliente. O backend é a barreira (admin pela matriz + 409
 * com conciliação em processamento); aqui é UX.
 */

import { Loader2 } from 'lucide-react';
import { useEffect, useId, useState } from 'react';
import { toast } from 'sonner';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useCloseClient } from '@/hooks/use-clients';
import { ApiError } from '@/lib/api/client';
import type { Client } from '@/lib/api/clients';

interface CloseClientDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  client: Client;
}

export function CloseClientDialog({ open, onOpenChange, client }: CloseClientDialogProps) {
  const mutation = useCloseClient(client.id);
  const inputId = useId();
  const [typed, setTyped] = useState('');

  useEffect(() => {
    if (!open) {
      setTyped('');
      mutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const isPending = mutation.isPending;
  const nameMatches = typed.trim() === client.name.trim();
  const count = client.reconciliation_count;

  async function handleConfirm() {
    if (!nameMatches) return;
    try {
      await mutation.mutateAsync();
      toast.success(`Cliente ${client.name} encerrado. O histórico fica disponível para consulta.`);
      onOpenChange(false);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível encerrar o cliente.',
      );
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Encerrar cliente</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="text-foreground font-medium">{client.name}</span> deixa de operar: o
            nome é anonimizado, as credenciais Omie e o conteúdo protegido são destruídos, e os
            usuários do cliente perdem o acesso. {count} conciliaç{count === 1 ? 'ão' : 'ões'} e os
            valores ficam como histórico, apenas para consulta. Esta ação é permanente: se o cliente
            voltar, será um cadastro novo.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-2">
          <Label htmlFor={inputId}>Digite o nome do cliente para confirmar</Label>
          <Input
            id={inputId}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={client.name}
            autoComplete="off"
            disabled={isPending}
          />
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>Cancelar</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={handleConfirm}
            disabled={isPending || !nameMatches}
          >
            {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            Encerrar cliente
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
