'use client';

/**
 * Gaveta de conectar / editar uma ORIGEM do cliente (Sprint 9 / R3 · R5).
 *
 * Uma gaveta para os dois modos, no shell do design-system
 * (`SheetHeader`/`SheetBody`/`SheetFooter`): header e rodapé fixos, miolo
 * rolando, **Cancelar à esquerda** e a ação primária à direita. Quem abre
 * remonta por `key` para o estado nascer limpo.
 *
 * **O gate do teste continua valendo** — e continua sendo só do front: desde a
 * 09.3 o servidor verifica a credencial contra o provedor antes de persistir,
 * então `POST` direto com credencial inválida não cria conexão `ativa` não
 * testada. O gate existe para o erro aparecer no formulário, e não como um
 * toast depois de o usuário achar que salvou.
 *
 * Na EDIÇÃO os dois blocos são independentes: dá para renomear sem mexer na
 * credencial. Se a credencial for tocada, o teste volta a ser obrigatório.
 *
 * **409 de `(tipo, rótulo)` repetido vira erro INLINE no campo rótulo**, com o
 * nome da origem que ocupa o par — resolvido a partir de
 * `details.existingConnectionId` contra a lista que a seção já tem em mãos.
 * Um toast genérico deixaria o usuário adivinhando qual rótulo trocar.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { useCreateConnection, useUpdateConnection } from '@/hooks/use-client-connections';
import { useTestConnection } from '@/hooks/use-clients';
import { ApiError } from '@/lib/api/client';
import {
  DEFAULT_PROVIDER_LABEL,
  EXISTING_CONNECTION_ID_KEY,
  omieCredentials,
  OMIE_PROVIDER_TYPE,
} from '@/lib/api/client-connections';
import type { ClientConnection } from '@/lib/contracts';
import {
  createConnectionSchema,
  updateConnectionSchema,
  type CreateConnectionFormValues,
  type UpdateConnectionFormValues,
} from '@/lib/validation/client-connections';

import { PasswordInput } from '../password-input';
import { TestConnectionButton, type TestConnectionState } from '../test-connection-button';

/**
 * Tipos oferecidos pelo seletor. Hoje só o Omie existe no registry do backend
 * (tipo desconhecido é 422) — a lista é um array para o 2º provedor entrar aqui
 * sem virar um `if` na tela.
 */
const PROVIDER_OPTIONS = [{ value: OMIE_PROVIDER_TYPE, label: 'Omie' }] as const;

interface ConnectionFormDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
  /** `null` = conectar uma origem nova; preenchida = editar aquela. */
  connection: ClientConnection | null;
  /** Lista atual — usada só para nomear a origem que ocupa o par no 409. */
  connections: readonly ClientConnection[];
}

export function ConnectionFormDrawer({
  open,
  onOpenChange,
  clientId,
  connection,
  connections,
}: ConnectionFormDrawerProps) {
  return connection === null ? (
    <CreateDrawer
      open={open}
      onOpenChange={onOpenChange}
      clientId={clientId}
      connections={connections}
    />
  ) : (
    <EditDrawer
      open={open}
      onOpenChange={onOpenChange}
      clientId={clientId}
      connection={connection}
      connections={connections}
    />
  );
}

/**
 * Traduz o 409 de par repetido em erro de CAMPO. Devolve `true` quando tratou —
 * o caller só cai no toast quando isto é `false`.
 */
function useLabelConflict(connections: readonly ClientConnection[]) {
  return (err: unknown): string | null => {
    if (!(err instanceof ApiError) || err.status !== 409) return null;
    const existingId = err.details[EXISTING_CONNECTION_ID_KEY];
    if (existingId === undefined) return null;
    const existing = connections.find((c) => c.id === existingId);
    return existing === undefined
      ? 'Já existe uma origem deste tipo com este rótulo neste cliente. Use outro rótulo.'
      : `O rótulo "${existing.label}" já está em uso por outra origem deste tipo neste cliente. Use outro.`;
  };
}

// ---------------------------------------------------------------------------
// Conectar
// ---------------------------------------------------------------------------

function CreateDrawer({
  open,
  onOpenChange,
  clientId,
  connections,
}: Omit<ConnectionFormDrawerProps, 'connection'>) {
  const createMutation = useCreateConnection(clientId);
  const testMutation = useTestConnection();
  const readLabelConflict = useLabelConflict(connections);

  const [showKey, setShowKey] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
  const [testState, setTestState] = useState<TestConnectionState>({ kind: 'idle' });
  // Ver `create-client-modal`: manter a última dupla testada num ref evita pôr
  // `testState` como dependência do efeito que rebobina para `idle`.
  const lastTestedRef = useRef<{ key: string; secret: string } | null>(null);

  const form = useForm<CreateConnectionFormValues>({
    resolver: zodResolver(createConnectionSchema),
    defaultValues: {
      provider_type: OMIE_PROVIDER_TYPE,
      label: '',
      app_key: '',
      app_secret: '',
    },
    mode: 'onSubmit',
  });

  const watchedKey = useWatch({ control: form.control, name: 'app_key' });
  const watchedSecret = useWatch({ control: form.control, name: 'app_secret' });
  const watchedType = useWatch({ control: form.control, name: 'provider_type' });

  useEffect(() => {
    if (lastTestedRef.current === null) return;
    const { key, secret } = lastTestedRef.current;
    if (watchedKey !== key || watchedSecret !== secret) {
      lastTestedRef.current = null;
      setTestState({ kind: 'idle' });
    }
  }, [watchedKey, watchedSecret]);

  async function handleTest() {
    const key = form.getValues('app_key').trim();
    const secret = form.getValues('app_secret').trim();
    if (!key || !secret) return;
    setTestState({ kind: 'testing' });
    try {
      const res = await testMutation.mutateAsync({ omie_app_key: key, omie_app_secret: secret });
      lastTestedRef.current = { key, secret };
      setTestState(res.ok ? { kind: 'success' } : { kind: 'failure', message: res.message });
    } catch (err) {
      lastTestedRef.current = { key, secret };
      setTestState({
        kind: 'failure',
        message: err instanceof ApiError ? err.userMessage : 'Não foi possível testar a conexão.',
      });
    }
  }

  async function onSubmit(values: CreateConnectionFormValues) {
    if (testState.kind !== 'success') {
      toast.error('Teste a conexão antes de salvar.');
      return;
    }
    const label = values.label.trim();
    try {
      await createMutation.mutateAsync({
        provider_type: values.provider_type,
        // Rótulo em branco = deixar o backend usar o padrão do tipo. Mandar
        // `""` seria 422 (`_clean_label`), e mandar o padrão daqui duplicaria a
        // regra de "só na primeira conexão daquele tipo".
        ...(label.length > 0 ? { label } : {}),
        credentials: omieCredentials(values.app_key.trim(), values.app_secret.trim()),
      });
      toast.success('Origem conectada.');
      onOpenChange(false);
    } catch (err) {
      const conflict = readLabelConflict(err);
      if (conflict !== null) {
        form.setError('label', { type: 'server', message: conflict });
        return;
      }
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível conectar a origem.',
      );
    }
  }

  const isSubmitting = createMutation.isPending;
  const isTesting = testState.kind === 'testing';
  const disabled = isSubmitting || isTesting;
  const canTest =
    !disabled && (watchedKey ?? '').trim().length > 0 && (watchedSecret ?? '').trim().length > 0;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="p-0">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="flex h-full flex-col" noValidate>
            <SheetHeader>
              <SheetTitle>Conectar origem</SheetTitle>
              <SheetDescription>
                As credenciais são verificadas contra o sistema de origem antes de qualquer
                gravação, e ficam criptografadas com a chave deste cliente.
              </SheetDescription>
            </SheetHeader>

            <SheetBody className="space-y-4">
              <FormField
                control={form.control}
                name="provider_type"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Tipo de origem</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange} disabled={disabled}>
                      <FormControl>
                        <SelectTrigger aria-label="Tipo de origem">
                          <SelectValue placeholder="Selecione o tipo" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {PROVIDER_OPTIONS.map((o) => (
                          <SelectItem key={o.value} value={o.value}>
                            {o.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="label"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Rótulo (opcional)</FormLabel>
                    <FormControl>
                      <Input
                        autoComplete="off"
                        disabled={disabled}
                        placeholder={DEFAULT_PROVIDER_LABEL[watchedType] ?? watchedType}
                        {...field}
                      />
                    </FormControl>
                    <FormDescription>
                      Como você chama esta origem. Um mesmo cliente pode ter mais de uma origem do
                      mesmo tipo — é o rótulo que as distingue.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="app_key"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>App Key Omie</FormLabel>
                    <FormControl>
                      <PasswordInput
                        visible={showKey}
                        onToggle={() => setShowKey((v) => !v)}
                        disabled={disabled}
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
                name="app_secret"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>App Secret Omie</FormLabel>
                    <FormControl>
                      <PasswordInput
                        visible={showSecret}
                        onToggle={() => setShowSecret((v) => !v)}
                        disabled={disabled}
                        autoComplete="off"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <TestConnectionButton state={testState} disabled={!canTest} onClick={handleTest} />
            </SheetBody>

            {/* Cancelar à ESQUERDA (`justify-between` do SheetFooter). */}
            <SheetFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isSubmitting}
              >
                Cancelar
              </Button>
              <Button type="submit" disabled={disabled || testState.kind !== 'success'}>
                {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                Salvar origem
              </Button>
            </SheetFooter>
          </form>
        </Form>
      </SheetContent>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------
// Editar
// ---------------------------------------------------------------------------

function EditDrawer({
  open,
  onOpenChange,
  clientId,
  connection,
  connections,
}: ConnectionFormDrawerProps & { connection: ClientConnection }) {
  const updateMutation = useUpdateConnection(clientId, connection.id);
  const testMutation = useTestConnection();
  const readLabelConflict = useLabelConflict(connections);

  const [showKey, setShowKey] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
  const [testState, setTestState] = useState<TestConnectionState>({ kind: 'idle' });
  const lastTestedRef = useRef<{ key: string; secret: string } | null>(null);

  const form = useForm<UpdateConnectionFormValues>({
    resolver: zodResolver(updateConnectionSchema),
    defaultValues: { label: connection.label, app_key: '', app_secret: '' },
    mode: 'onSubmit',
  });

  const watchedLabel = useWatch({ control: form.control, name: 'label' });
  const watchedKey = useWatch({ control: form.control, name: 'app_key' });
  const watchedSecret = useWatch({ control: form.control, name: 'app_secret' });

  useEffect(() => {
    if (lastTestedRef.current === null) return;
    const { key, secret } = lastTestedRef.current;
    if (watchedKey !== key || watchedSecret !== secret) {
      lastTestedRef.current = null;
      setTestState({ kind: 'idle' });
    }
  }, [watchedKey, watchedSecret]);

  async function handleTest() {
    const key = (form.getValues('app_key') ?? '').trim();
    const secret = (form.getValues('app_secret') ?? '').trim();
    if (!key || !secret) return;
    setTestState({ kind: 'testing' });
    try {
      const res = await testMutation.mutateAsync({ omie_app_key: key, omie_app_secret: secret });
      lastTestedRef.current = { key, secret };
      setTestState(res.ok ? { kind: 'success' } : { kind: 'failure', message: res.message });
    } catch (err) {
      lastTestedRef.current = { key, secret };
      setTestState({
        kind: 'failure',
        message: err instanceof ApiError ? err.userMessage : 'Não foi possível testar a conexão.',
      });
    }
  }

  const labelChanged = (watchedLabel ?? '').trim() !== connection.label;
  const credentialsTouched =
    (watchedKey ?? '').trim().length > 0 || (watchedSecret ?? '').trim().length > 0;

  async function onSubmit(values: UpdateConnectionFormValues) {
    const label = (values.label ?? '').trim();
    const key = (values.app_key ?? '').trim();
    const secret = (values.app_secret ?? '').trim();
    const wantsCredentials = key.length > 0 && secret.length > 0;

    if (credentialsTouched && testState.kind !== 'success') {
      toast.error('Teste a conexão antes de salvar a credencial nova.');
      return;
    }

    // Corpo vazio é 422 no backend — um PATCH que não pede nada é engano de
    // quem clicou. O botão já fica `disabled`, e isto cobre o submit por Enter.
    if (!wantsCredentials && label === connection.label) {
      toast.error('Nada para salvar: mude o rótulo ou informe a credencial nova.');
      return;
    }

    try {
      await updateMutation.mutateAsync({
        ...(label !== connection.label ? { label } : {}),
        ...(wantsCredentials ? { credentials: omieCredentials(key, secret) } : {}),
      });
      toast.success('Origem atualizada.');
      onOpenChange(false);
    } catch (err) {
      const conflict = readLabelConflict(err);
      if (conflict !== null) {
        form.setError('label', { type: 'server', message: conflict });
        return;
      }
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível atualizar a origem.',
      );
    }
  }

  const isSubmitting = updateMutation.isPending;
  const isTesting = testState.kind === 'testing';
  const disabled = isSubmitting || isTesting;
  const canTest =
    !disabled && (watchedKey ?? '').trim().length > 0 && (watchedSecret ?? '').trim().length > 0;
  const canSubmit =
    !disabled &&
    (labelChanged || credentialsTouched) &&
    (!credentialsTouched || testState.kind === 'success');

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="p-0">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="flex h-full flex-col" noValidate>
            <SheetHeader>
              <SheetTitle>Editar origem</SheetTitle>
              <SheetDescription>
                Renomeie a origem, troque a credencial, ou as duas coisas. Deixar as credenciais em
                branco mantém as atuais.
              </SheetDescription>
            </SheetHeader>

            <SheetBody className="space-y-4">
              <FormField
                control={form.control}
                name="label"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Rótulo</FormLabel>
                    <FormControl>
                      <Input autoComplete="off" disabled={disabled} {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="app_key"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>App Key Omie</FormLabel>
                    <FormControl>
                      <PasswordInput
                        visible={showKey}
                        onToggle={() => setShowKey((v) => !v)}
                        disabled={disabled}
                        autoComplete="off"
                        placeholder="••••••••"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="app_secret"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>App Secret Omie</FormLabel>
                    <FormControl>
                      <PasswordInput
                        visible={showSecret}
                        onToggle={() => setShowSecret((v) => !v)}
                        disabled={disabled}
                        autoComplete="off"
                        placeholder="••••••••"
                        {...field}
                      />
                    </FormControl>
                    <FormDescription>
                      A credencial é trocada por inteiro — informe App Key e App Secret juntos.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <TestConnectionButton state={testState} disabled={!canTest} onClick={handleTest} />
            </SheetBody>

            <SheetFooter>
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
                Salvar alterações
              </Button>
            </SheetFooter>
          </form>
        </Form>
      </SheetContent>
    </Sheet>
  );
}
