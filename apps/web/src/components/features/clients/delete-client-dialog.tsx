'use client';

/**
 * Exclusão DEFINITIVA de cliente (86e34jd1d) — decisão do Galhardo em
 * 04/09/2026: quando o cliente sai da carteira, some da plataforma; se voltar,
 * integra de novo.
 *
 * É a ação mais destrutiva da tela: leva conciliações, usuários do cliente,
 * glossário, credenciais Omie e favoritos. Por isso passa por `AlertDialog`
 * (ADR-006-FE: não fecha por clique fora, foco inicial no Cancelar) E exige
 * digitar o nome do cliente — um clique errado não pode apagar meses de
 * trabalho. O backend é a barreira (admin pela matriz + 409 com conciliação em
 * processamento); aqui é UX.
 */

import { Loader2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
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
import { useDeleteClient } from '@/hooks/use-clients';
import { ApiError } from '@/lib/api/client';
import type { Client } from '@/lib/api/clients';

interface DeleteClientDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  client: Client;
}

export function DeleteClientDialog({ open, onOpenChange, client }: DeleteClientDialogProps) {
  const router = useRouter();
  const mutation = useDeleteClient(client.id);
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
      toast.success(`Cliente ${client.name} excluído.`);
      onOpenChange(false);
      router.replace('/clientes');
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível excluir o cliente.',
      );
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Excluir cliente</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="text-foreground font-medium">{client.name}</span> será apagado da
            plataforma com {count} conciliaç{count === 1 ? 'ão' : 'ões'}, os usuários do cliente, o
            glossário, as credenciais Omie e os favoritos. Esta ação é permanente: para voltar, será
            preciso integrar o cliente de novo.
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
            Excluir definitivamente
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
