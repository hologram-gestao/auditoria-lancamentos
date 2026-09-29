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
 *
 * **Origem SEM credencial (Sprint 14 / R1, `arquivo`).** O tipo escolhido
 * decide, por `providerRequiresCredentials` (a tabela única do front, espelho
 * de `requires_credentials` do backend), se os campos de App Key/Secret e o
 * gate do teste existem: para `arquivo` não há segredo a verificar — a conexão
 * nasce ativa e o servidor responderia 409 `CAPACIDADE_AUSENTE` ao teste. Na
 * EDIÇÃO a pergunta é feita à CAPACIDADE da conexão que a API devolveu
 * (`connectionRequiresCredentials`), nunca ao tipo comparado à mão.
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
  connectionRequiresCredentials,
  DEFAULT_PROVIDER_LABEL,
  EXISTING_CONNECTION_ID_KEY,
  omieCredentials,
  OMIE_PROVIDER_TYPE,
  PROVIDER_TYPES,
  providerRequiresCredentials,
} from '@/lib/api/client-connections';
import type { ClientConnection } from '@/lib/contracts';
import { connectableProviderTypes } from '@/lib/origin-capabilities';
import {
  createConnectionSchema,
  updateConnectionSchema,
  type CreateConnectionFormValues,
  type UpdateConnectionFormValues,
} from '@/lib/validation/client-connections';

import { PasswordInput } from '../password-input';
import { TestConnectionButton, type TestConnectionState } from '../test-connection-button';

interface ConnectionFormDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
  /** `null` = conectar uma origem nova; preenchida = editar aquela. */
  connection: ClientConnection | null;
  /** Lista atual — usada só para nomear a origem que ocupa o par no 409. */
  connections: readonly ClientConnection[];
  /**
   * Tipo já escolhido ao abrir para CONECTAR, quando quem mandou abrir sabe
   * qual origem falta (a aba "Origem por arquivo" manda `arquivo`). Sem ele o
   * formulário nasce no padrão. Ignorado ao EDITAR: lá o tipo é o da conexão.
   */
  initialProviderType?: string | null;
}

export function ConnectionFormDrawer({
  open,
  onOpenChange,
  clientId,
  connection,
  connections,
  initialProviderType = null,
}: ConnectionFormDrawerProps) {
  return connection === null ? (
    <CreateDrawer
      open={open}
      onOpenChange={onOpenChange}
      clientId={clientId}
      connections={connections}
      initialProviderType={initialProviderType}
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

/**
 * O tipo com que o formulário de CONECTAR nasce: o pedido pela URL (`?conectar=`)
 * quando ele é oferecível neste cliente, senão o primeiro oferecível. Nunca um
 * valor fora da lista — o select ficaria vazio e o schema recusaria no submit.
 */
function seedableProviderType(
  requested: string | null,
  connections: readonly ClientConnection[],
): string {
  const options = connectableProviderTypes(PROVIDER_TYPES, connections);
  const wanted = options.find((o) => o.value === requested);
  return wanted?.value ?? options[0]?.value ?? OMIE_PROVIDER_TYPE;
}

function CreateDrawer({
  open,
  onOpenChange,
  clientId,
  connections,
  initialProviderType = null,
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
      // Só um tipo CONHECIDO entra: `?conectar=` vem da URL, e um valor
      // inventado deixaria o select num estado que o schema recusa no submit.
      // O tipo semeado pela URL (`?conectar=`) só vale se ELE for oferecível
      // aqui; senão o select nasceria num valor que nem está na lista.
      provider_type: seedableProviderType(initialProviderType, connections),
      label: '',
      app_key: '',
      app_secret: '',
    },
    mode: 'onSubmit',
  });

  const watchedKey = useWatch({ control: form.control, name: 'app_key' });
  const watchedSecret = useWatch({ control: form.control, name: 'app_secret' });
  const watchedType = useWatch({ control: form.control, name: 'provider_type' });
  // A tabela única decide: `arquivo` não tem segredo, então os campos e o gate
  // do teste nem existem para ele (§4.9: o teste responderia 409).
  const requiresCredentials = providerRequiresCredentials(watchedType);
  const typeOption = PROVIDER_TYPES.find((option) => option.value === watchedType);
  // 86e3g9u3w: só os tipos que o servidor aceitaria neste cliente. Um cliente
  // tem um tipo de origem de lançamentos só (§4.8): oferecer o outro é oferecer
  // um 409 `ORIGEM_JA_CONECTADA` depois do formulário inteiro preenchido.
  const typeOptions = connectableProviderTypes(PROVIDER_TYPES, connections);

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

  async function onSubmit(values: CreateConnectionFormValues) {
    const needsCredentials = providerRequiresCredentials(values.provider_type);
    if (needsCredentials && testState.kind !== 'success') {
      toast.error('Teste a conexão antes de salvar.');
      return;
    }
    const label = values.label.trim();
    try {
      await createMutation.mutateAsync({
        provider_type: values.provider_type,
        // Rótulo em branco = deixar o backend usar o padrão do tipo. Mandar
        // `""` seria 400 (`_clean_label`), e mandar o padrão daqui duplicaria a
        // regra de "só na primeira conexão daquele tipo".
        ...(label.length > 0 ? { label } : {}),
        // Credencial só para quem a tem: mandá-la para `arquivo` é 400 de forma
        // (`assert_credentials_shape`), e omiti-la para o Omie também.
        ...(needsCredentials
          ? {
              credentials: omieCredentials(
                (values.app_key ?? '').trim(),
                (values.app_secret ?? '').trim(),
              ),
            }
          : {}),
      });
      toast.success(
        needsCredentials
          ? 'Origem conectada.'
          : 'Origem por arquivo conectada. Configure o mapeamento de colunas na aba "Origem por arquivo".',
      );
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
  const canSubmit = !disabled && (!requiresCredentials || testState.kind === 'success');

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="p-0">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="flex h-full flex-col" noValidate>
            <SheetHeader>
              <SheetTitle>Conectar origem</SheetTitle>
              <SheetDescription>
                {requiresCredentials
                  ? 'As credenciais são verificadas contra o sistema de origem antes de qualquer gravação, e ficam criptografadas com a chave deste cliente.'
                  : 'A origem por arquivo não tem credencial: a planilha ou o extrato do mês é lido no envio, pelo mapeamento de colunas que você configura depois de conectar.'}
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
                        {typeOptions.map((o) => (
                          <SelectItem key={o.value} value={o.value}>
                            {o.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {typeOption !== undefined && (
                      <FormDescription>{typeOption.description}</FormDescription>
                    )}
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

              {requiresCredentials && (
                <>
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

                  <TestConnectionButton
                    state={testState}
                    disabled={!canTest}
                    onClick={handleTest}
                  />
                </>
              )}
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
              <Button type="submit" disabled={!canSubmit}>
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
  // Pela CAPACIDADE que a API devolveu: origem sem `verificar_credencial`
  // (`arquivo`) não tem o que trocar nem testar — só o rótulo.
  const hasCredentials = connectionRequiresCredentials(connection);

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
                {hasCredentials
                  ? 'Renomeie a origem, troque a credencial, ou as duas coisas. Deixar as credenciais em branco mantém as atuais.'
                  : 'Renomeie a origem. Esta origem não tem credencial: o que ela lê é o arquivo enviado a cada mês.'}
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

              {hasCredentials && (
                <>
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

                  <TestConnectionButton
                    state={testState}
                    disabled={!canTest}
                    onClick={handleTest}
                  />
                </>
              )}
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
