'use client';

/**
 * Modal "Novo Cliente" — Doc §9.2.
 *
 * Fluxo:
 *   1. Preenche nome, app key, app secret.
 *   2. Clica "Testar conexão" — backend valida sem persistir.
 *   3. Salvar só fica habilitado APÓS sucesso do teste (UX guard, mas o backend
 *      não bloqueia tecnicamente — confia que o front chamou).
 *   4. Editar a key/secret após o teste invalida o sucesso e exige novo teste.
 *
 * Erros tratados:
 *   - Falha do test-connection → `ok=false` (200) → mensagem inline (não joga no toast).
 *   - Falha de transporte/rede no teste → `ApiError` → mensagem inline também.
 *   - Falha no `createClient` → toast destrutivo com `userMessage` do backend.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { useCreateClient, useTestConnection } from '@/hooks/use-clients';
import { ApiError } from '@/lib/api/client';
import { createClientSchema, type CreateClientFormValues } from '@/lib/validation/clients';

import { PasswordInput } from './password-input';
import { TestConnectionButton, type TestConnectionState } from './test-connection-button';

interface CreateClientModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateClientModal({ open, onOpenChange }: CreateClientModalProps) {
  const [showKey, setShowKey] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
  const [testState, setTestState] = useState<TestConnectionState>({ kind: 'idle' });
  // Última dupla submetida ao test (sucesso OU falha). Ao editar key/secret
  // o useEffect compara contra esse ref e volta a idle. Manter num ref evita
  // o testState como dependência do effect — se ele estivesse, setar `failure`
  // dispararia o effect que rebobina pra idle antes da UI renderizar a mensagem.
  const lastTestedRef = useRef<{ key: string; secret: string } | null>(null);

  const createMutation = useCreateClient();
  const testMutation = useTestConnection();

  const form = useForm<CreateClientFormValues>({
    resolver: zodResolver(createClientSchema),
    defaultValues: { name: '', omie_app_key: '', omie_app_secret: '' },
    mode: 'onSubmit',
  });

  const watchedKey = useWatch({ control: form.control, name: 'omie_app_key' });
  const watchedSecret = useWatch({ control: form.control, name: 'omie_app_secret' });

  // Reset completo quando o modal fecha — não vaza credenciais entre aberturas.
  useEffect(() => {
    if (!open) {
      form.reset();
      setShowKey(false);
      setShowSecret(false);
      setTestState({ kind: 'idle' });
      lastTestedRef.current = null;
      createMutation.reset();
      testMutation.reset();
    }
    // form/mutations são estáveis; rodar só quando o modal abrir/fechar.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Volta a `idle` quando o usuário edita key/secret APÓS um teste (success
  // ou failure). Reage só a mudanças nos campos — testState NÃO é dependência.
  useEffect(() => {
    if (lastTestedRef.current === null) return;
    const { key, secret } = lastTestedRef.current;
    if (watchedKey !== key || watchedSecret !== secret) {
      lastTestedRef.current = null;
      setTestState({ kind: 'idle' });
    }
  }, [watchedKey, watchedSecret]);

  async function handleTest() {
    const key = form.getValues('omie_app_key').trim();
    const secret = form.getValues('omie_app_secret').trim();
    if (!key || !secret) {
      // Defesa: o botão fica disabled, mas se chegou aqui evita request inútil.
      return;
    }
    setTestState({ kind: 'testing' });
    try {
      const res = await testMutation.mutateAsync({
        omie_app_key: key,
        omie_app_secret: secret,
      });
      lastTestedRef.current = { key, secret };
      setTestState(res.ok ? { kind: 'success' } : { kind: 'failure', message: res.message });
    } catch (err) {
      lastTestedRef.current = { key, secret };
      const message =
        err instanceof ApiError ? err.userMessage : 'Não foi possível testar a conexão.';
      setTestState({ kind: 'failure', message });
    }
  }

  async function onSubmit(values: CreateClientFormValues) {
    if (testState.kind !== 'success') {
      // UX guard — não deveria atingir esse caminho com o botão disabled.
      return;
    }
    try {
      await createMutation.mutateAsync(values);
      toast.success('Cliente criado.');
      onOpenChange(false);
    } catch (err) {
      const msg = err instanceof ApiError ? err.userMessage : 'Não foi possível criar o cliente.';
      toast.error(msg);
    }
  }

  const isSubmitting = createMutation.isPending;
  const isTesting = testState.kind === 'testing';
  const inputsDisabled = isSubmitting || isTesting;

  const canTest =
    !inputsDisabled &&
    (watchedKey ?? '').trim().length > 0 &&
    (watchedSecret ?? '').trim().length > 0;

  const canSubmit =
    testState.kind === 'success' &&
    !isSubmitting &&
    !isTesting &&
    form.getValues('name').trim().length > 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Novo Cliente</DialogTitle>
          <DialogDescription>
            As credenciais Omie são criptografadas e nunca persistem em texto plano.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Nome do cliente</FormLabel>
                  <FormControl>
                    <Input
                      autoComplete="off"
                      autoFocus
                      disabled={inputsDisabled}
                      placeholder="Como a Hologram se refere ao cliente"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="omie_app_key"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>App Key Omie</FormLabel>
                  <FormControl>
                    <PasswordInput
                      visible={showKey}
                      onToggle={() => setShowKey((v) => !v)}
                      disabled={inputsDisabled}
                      autoComplete="off"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="omie_app_secret"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>App Secret Omie</FormLabel>
                  <FormControl>
                    <PasswordInput
                      visible={showSecret}
                      onToggle={() => setShowSecret((v) => !v)}
                      disabled={inputsDisabled}
                      autoComplete="off"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <TestConnectionButton state={testState} disabled={!canTest} onClick={handleTest} />

            <DialogFooter className="gap-2 sm:gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isSubmitting}
              >
                Cancelar
              </Button>
              <Button type="submit" disabled={!canSubmit}>
                {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                Salvar
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
