'use client';

/**
 * Editor do MAPEAMENTO DE ENTRADA, em gaveta (Sprint 14 — FRONT 14.5 / R1 · R5).
 *
 * Fluxo em dois passos, e o segundo depende do primeiro:
 *
 *   1. **Inspecionar** — a pessoa escolhe um arquivo do cliente; a tela chama
 *      `POST …/file-origin/inspect` e mostra as colunas do cabeçalho e uma
 *      amostra das primeiras linhas (só nesta resposta; nada persiste). Para CSV
 *      sem mapeamento salvo, delimitador e codificação são DECLARADOS antes de
 *      inspecionar — o servidor não fareja.
 *   2. **Mapear** — cada campo (data, descrição, valor, categoria, conta,
 *      documento) é um seletor populado pelas colunas encontradas; formato de
 *      data, separador decimal e a CONVENÇÃO DE SINAL (obrigatória, sem padrão:
 *      inferir sinal é o que o PRD proíbe) com os campos dependentes de cada
 *      convenção. Zod + react-hook-form espelham o backend 1:1
 *      (`lib/validation/input-mapping.ts`).
 *
 * Salvar é `PUT` (cria ou substitui). Se JÁ existe mapeamento, gravar exige a
 * confirmação explícita num `AlertDialog`: alterar muda como TODOS os próximos
 * arquivos serão lidos (R5). Ao editar um mapeamento salvo, os seletores já
 * oferecem as colunas gravadas — inspecionar um arquivo novo acrescenta as
 * encontradas nele.
 *
 * Shell do design-system: header fixo, miolo rola, **Cancelar à esquerda**.
 * Erro 400 do servidor (forma inválida) vira mensagem amigável; nunca o texto
 * interno. Quem abre remonta por `key` para o estado nascer limpo.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { FileSearch, Loader2 } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { toast } from 'sonner';

import { FileInputField } from '@/components/shared/file-input-field';
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
import { Label } from '@/components/ui/label';
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
import {
  Table,
  TableBody,
  TableCard,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useInspectFile, useSaveInputMapping } from '@/hooks/use-client-file-origin';
import { ApiError } from '@/lib/api/client';
import type {
  CsvDelimiter,
  FileInspectResult,
  InputEncoding,
  InputMapping,
  SignConvention,
} from '@/lib/contracts';
import { readFileRefusal } from '@/lib/file-origin-errors';
import {
  CATEGORY_MODE_LABELS,
  CATEGORY_MODES,
  CSV_DELIMITER_LABELS,
  CSV_DELIMITERS,
  DATE_FORMAT_LABELS,
  DATE_FORMATS,
  DECIMAL_SEPARATOR_LABELS,
  DECIMAL_SEPARATORS,
  defaultInputMappingFormValues,
  ENCODING_LABELS,
  ENCODINGS,
  FILE_FORMAT_LABELS,
  FILE_FORMATS,
  fromInputMapping,
  MAPPING_FIELD_LABELS,
  SIGN_CONVENTION_DESCRIPTIONS,
  SIGN_CONVENTION_LABELS,
  SIGN_CONVENTIONS,
  toInputMappingRequest,
  type InputMappingFormValues,
  type MappingColumnField,
} from '@/lib/input-mapping';
import { isOriginError } from '@/lib/origin-state';
import { inputMappingFormSchema } from '@/lib/validation/input-mapping';

import { FILE_ACCEPT, FileRefusalNotice, isCsvFileName } from './file-refusal-notice';

/**
 * O `Select` do Radix não aceita item com valor `""`: o "nenhuma coluna" das
 * colunas opcionais viaja com este marcador e volta a `''` no formulário.
 */
const NONE = '__none__';

interface InputMappingEditorDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
  /** `null` = criar; preenchido = alterar (exige confirmação ao gravar). */
  mapping: InputMapping | null;
  /**
   * Arquivo que o envio já tinha em mãos quando descobriu que não havia
   * mapeamento (FRONT 14.6): a gaveta abre e inspeciona ELE, sem pedir de novo.
   */
  seedFile?: File | null;
  onSaved?: (mapping: InputMapping) => void;
}

export function InputMappingEditorDrawer({
  open,
  onOpenChange,
  clientId,
  mapping,
  seedFile = null,
  onSaved,
}: InputMappingEditorDrawerProps) {
  const inspectMutation = useInspectFile(clientId);
  const saveMutation = useSaveInputMapping(clientId);

  const [file, setFile] = useState<File | null>(seedFile);
  const [inspection, setInspection] = useState<FileInspectResult | null>(null);
  const [inspectRefusal, setInspectRefusal] = useState<unknown>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  // Declarados para a INSPEÇÃO de um CSV sem mapeamento (o servidor não fareja).
  const [inspectDelimiter, setInspectDelimiter] = useState<CsvDelimiter>(';');
  const [inspectEncoding, setInspectEncoding] = useState<InputEncoding>('utf-8-sig');
  const seedInspected = useRef(false);

  const form = useForm<InputMappingFormValues>({
    resolver: zodResolver(inputMappingFormSchema),
    defaultValues: mapping ? fromInputMapping(mapping) : defaultInputMappingFormValues(),
    mode: 'onSubmit',
  });

  const fileFormat = useWatch({ control: form.control, name: 'fileFormat' });
  const signConvention = useWatch({ control: form.control, name: 'signConvention' });
  const categoryColumn = useWatch({ control: form.control, name: 'categoryColumn' });
  const watchedColumns = useWatch({
    control: form.control,
    name: [
      'dateColumn',
      'descriptionColumn',
      'amountColumn',
      'categoryColumn',
      'accountColumn',
      'documentColumn',
      'natureColumn',
      'debitColumn',
      'creditColumn',
    ],
  });

  /**
   * As opções dos seletores: as colunas INSPECIONADAS mais as já escolhidas
   * (as do mapeamento salvo, ao alterar) — assim editar um mapeamento não exige
   * inspecionar de novo, e inspecionar um arquivo novo só acrescenta.
   */
  const columnOptions = useMemo(() => {
    const set = new Set<string>();
    for (const column of inspection?.columns ?? []) set.add(column);
    for (const column of watchedColumns) if (column) set.add(column);
    return Array.from(set);
  }, [inspection, watchedColumns]);

  const hasColumns = columnOptions.length > 0;
  const isCsvFile = file !== null && isCsvFileName(file.name);
  const isBusy = inspectMutation.isPending || saveMutation.isPending;

  async function handleInspect(target: File) {
    setInspectRefusal(null);
    try {
      const result = await inspectMutation.mutateAsync({
        file: target,
        // Com mapeamento salvo o servidor usa o delimitador/codificação DELE;
        // sem mapeamento, os declarados aqui (padrão `;` e `utf-8-sig`).
        csvDelimiter: mapping === null && isCsvFileName(target.name) ? inspectDelimiter : null,
        encoding: mapping === null && isCsvFileName(target.name) ? inspectEncoding : null,
      });
      setInspection(result);
      // O formato vem DETECTADO pelo contêiner do arquivo; para CSV os campos
      // de delimitador/codificação do MAPEAMENTO nascem com o que foi declarado
      // na inspeção — é o que acabou de funcionar para ler o arquivo.
      form.setValue('fileFormat', result.format, { shouldDirty: true });
      if (result.format === 'csv') {
        if (form.getValues('csvDelimiter') === '') form.setValue('csvDelimiter', inspectDelimiter);
        if (form.getValues('encoding') === '') form.setValue('encoding', inspectEncoding);
      } else {
        form.setValue('csvDelimiter', '');
        form.setValue('encoding', '');
      }
    } catch (err) {
      if (readFileRefusal(err) !== null || isOriginError(err)) {
        setInspectRefusal(err);
        return;
      }
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível inspecionar o arquivo.',
      );
    }
  }

  // A gaveta que abre pelo envio (sem mapeamento) já tem o arquivo: inspeciona
  // uma vez, sem pedir de novo. Ref para não repetir a cada render.
  useEffect(() => {
    if (!open || seedFile === null || seedInspected.current) return;
    seedInspected.current = true;
    void handleInspect(seedFile);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, seedFile]);

  async function persist(values: InputMappingFormValues) {
    try {
      const result = await saveMutation.mutateAsync(toInputMappingRequest(values));
      toast.success(
        result.created
          ? 'Mapeamento de colunas salvo.'
          : 'Mapeamento alterado. Os próximos arquivos serão lidos com ele.',
      );
      onSaved?.(result.mapping);
      setConfirmOpen(false);
      onOpenChange(false);
    } catch (err) {
      setConfirmOpen(false);
      if (err instanceof ApiError && err.code === 'VALIDATION_ERROR') {
        // O 400 é genérico de propósito (§4.8): a instrução tem de vir daqui.
        toast.error(
          'O servidor recusou o mapeamento: confira a convenção de sinal e os campos que ela exige.',
        );
        return;
      }
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível salvar o mapeamento.',
      );
    }
  }

  function onSubmit(values: InputMappingFormValues) {
    if (mapping !== null) {
      // Alterar muda a leitura de TODOS os próximos arquivos: confirmação explícita.
      setConfirmOpen(true);
      return;
    }
    void persist(values);
  }

  const usesAmount = signConvention !== 'colunas_separadas';
  const usesNature = signConvention === 'coluna_natureza';
  const usesSeparate = signConvention === 'colunas_separadas';

  return (
    <Sheet open={open} onOpenChange={(next) => !isBusy && onOpenChange(next)}>
      <SheetContent side="right" className="flex flex-col p-0 sm:max-w-2xl">
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="flex min-h-0 flex-1 flex-col"
            noValidate
          >
            <SheetHeader>
              <SheetTitle>{mapping ? 'Alterar mapeamento' : 'Configurar mapeamento'}</SheetTitle>
              <SheetDescription>
                Escolha um arquivo do cliente para ver as colunas dele; depois diga qual coluna é
                cada campo e como o arquivo indica débito e crédito. Nada é inferido.
              </SheetDescription>
            </SheetHeader>

            <SheetBody className="space-y-6">
              {/* Passo 1 — inspecionar. */}
              <section aria-labelledby="inspect-heading" className="space-y-3">
                <h3 id="inspect-heading" className="text-sm font-semibold">
                  1. Arquivo de exemplo
                </h3>
                <div className="space-y-1.5">
                  <Label htmlFor="mapping-inspect-file">Arquivo (.csv ou .xlsx)</Label>
                  <FileInputField
                    id="mapping-inspect-file"
                    accept={FILE_ACCEPT}
                    disabled={isBusy}
                    value={file}
                    onChange={(next) => {
                      setFile(next);
                      setInspection(null);
                      setInspectRefusal(null);
                    }}
                  />
                  <p className="text-muted-foreground text-xs">
                    Só as colunas e uma amostra das primeiras linhas são lidas; nada é gravado nesta
                    etapa.
                  </p>
                </div>

                {/* CSV sem mapeamento: delimitador e codificação DECLARADOS antes
                    de inspecionar — o servidor não fareja. */}
                {mapping === null && isCsvFile && (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="space-y-1.5">
                      <Label htmlFor="inspect-delimiter">Delimitador do CSV</Label>
                      <Select
                        value={inspectDelimiter}
                        onValueChange={(v) => setInspectDelimiter(v as CsvDelimiter)}
                        disabled={isBusy}
                      >
                        <SelectTrigger id="inspect-delimiter">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {CSV_DELIMITERS.map((d) => (
                            <SelectItem key={d} value={d}>
                              {CSV_DELIMITER_LABELS[d]}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="inspect-encoding">Codificação do CSV</Label>
                      <Select
                        value={inspectEncoding}
                        onValueChange={(v) => setInspectEncoding(v as InputEncoding)}
                        disabled={isBusy}
                      >
                        <SelectTrigger id="inspect-encoding">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {ENCODINGS.map((enc) => (
                            <SelectItem key={enc} value={enc}>
                              {ENCODING_LABELS[enc]}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                )}

                <Button
                  type="button"
                  variant="secondary"
                  disabled={file === null || isBusy}
                  onClick={() => file && void handleInspect(file)}
                >
                  {inspectMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <FileSearch className="h-4 w-4" aria-hidden="true" />
                  )}
                  {inspectMutation.isPending ? 'Inspecionando…' : 'Inspecionar arquivo'}
                </Button>

                {inspectRefusal !== null && (
                  <FileRefusalNotice error={inspectRefusal} clientId={clientId} />
                )}

                {inspection && <InspectionSample inspection={inspection} />}
              </section>

              {/* Passo 2 — mapear. Sem coluna nenhuma (novo, ainda sem inspeção)
                  os seletores não têm o que oferecer: a instrução fica no lugar. */}
              <section aria-labelledby="mapping-fields-heading" className="space-y-4">
                <h3 id="mapping-fields-heading" className="text-sm font-semibold">
                  2. Colunas e convenções
                </h3>
                {!hasColumns && (
                  <p
                    className="bg-info-muted text-info ring-info/30 rounded-lg p-3 text-sm ring-1 ring-inset"
                    role="status"
                  >
                    Inspecione um arquivo para escolher as colunas.
                  </p>
                )}

                <div className="grid gap-4 sm:grid-cols-2">
                  <FormField
                    control={form.control}
                    name="fileFormat"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Formato do arquivo</FormLabel>
                        <Select
                          value={field.value}
                          onValueChange={field.onChange}
                          disabled={isBusy}
                        >
                          <FormControl>
                            <SelectTrigger aria-label="Formato do arquivo">
                              <SelectValue />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {FILE_FORMATS.map((f) => (
                              <SelectItem key={f} value={f}>
                                {FILE_FORMAT_LABELS[f]}
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
                    name="dateFormat"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Formato da data</FormLabel>
                        <Select
                          value={field.value}
                          onValueChange={field.onChange}
                          disabled={isBusy}
                        >
                          <FormControl>
                            <SelectTrigger aria-label="Formato da data">
                              <SelectValue />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {DATE_FORMATS.map((f) => (
                              <SelectItem key={f} value={f}>
                                {DATE_FORMAT_LABELS[f]}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  {fileFormat === 'csv' && (
                    <>
                      <FormField
                        control={form.control}
                        name="csvDelimiter"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Delimitador do CSV</FormLabel>
                            <Select
                              value={field.value === '' ? undefined : field.value}
                              onValueChange={field.onChange}
                              disabled={isBusy}
                            >
                              <FormControl>
                                <SelectTrigger aria-label="Delimitador do CSV">
                                  <SelectValue placeholder="Escolha" />
                                </SelectTrigger>
                              </FormControl>
                              <SelectContent>
                                {CSV_DELIMITERS.map((d) => (
                                  <SelectItem key={d} value={d}>
                                    {CSV_DELIMITER_LABELS[d]}
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
                        name="encoding"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Codificação do CSV</FormLabel>
                            <Select
                              value={field.value === '' ? undefined : field.value}
                              onValueChange={field.onChange}
                              disabled={isBusy}
                            >
                              <FormControl>
                                <SelectTrigger aria-label="Codificação do CSV">
                                  <SelectValue placeholder="Escolha" />
                                </SelectTrigger>
                              </FormControl>
                              <SelectContent>
                                {ENCODINGS.map((enc) => (
                                  <SelectItem key={enc} value={enc}>
                                    {ENCODING_LABELS[enc]}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </>
                  )}
                  <FormField
                    control={form.control}
                    name="decimalSeparator"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Separador decimal</FormLabel>
                        <Select
                          value={field.value}
                          onValueChange={field.onChange}
                          disabled={isBusy}
                        >
                          <FormControl>
                            <SelectTrigger aria-label="Separador decimal">
                              <SelectValue />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {DECIMAL_SEPARATORS.map((s) => (
                              <SelectItem key={s} value={s}>
                                {DECIMAL_SEPARATOR_LABELS[s]}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                  <ColumnField
                    form={form}
                    name="dateColumn"
                    options={columnOptions}
                    disabled={isBusy}
                    required
                  />
                  <ColumnField
                    form={form}
                    name="descriptionColumn"
                    options={columnOptions}
                    disabled={isBusy}
                    required
                  />
                  <ColumnField
                    form={form}
                    name="categoryColumn"
                    options={columnOptions}
                    disabled={isBusy}
                    description="Ausente = toda linha nasce «sem categoria de origem» no de-para."
                  />
                  {categoryColumn !== '' && (
                    <FormField
                      control={form.control}
                      name="categoryMode"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Como ler a categoria</FormLabel>
                          <Select
                            value={field.value}
                            onValueChange={field.onChange}
                            disabled={isBusy}
                          >
                            <FormControl>
                              <SelectTrigger aria-label="Como ler a categoria">
                                <SelectValue />
                              </SelectTrigger>
                            </FormControl>
                            <SelectContent>
                              {CATEGORY_MODES.map((m) => (
                                <SelectItem key={m} value={m}>
                                  {CATEGORY_MODE_LABELS[m]}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  )}
                  <ColumnField
                    form={form}
                    name="accountColumn"
                    options={columnOptions}
                    disabled={isBusy}
                  />
                  <ColumnField
                    form={form}
                    name="documentColumn"
                    options={columnOptions}
                    disabled={isBusy}
                  />
                </div>

                {/* Convenção de sinal — obrigatória, sem padrão. */}
                <FormField
                  control={form.control}
                  name="signConvention"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Como o arquivo indica débito e crédito</FormLabel>
                      <FormControl>
                        <SignConventionGroup
                          value={field.value}
                          onChange={field.onChange}
                          disabled={isBusy}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                {signConvention !== '' && (
                  <div className="grid gap-4 sm:grid-cols-2" data-testid="sign-dependent-fields">
                    {usesAmount && (
                      <ColumnField
                        form={form}
                        name="amountColumn"
                        options={columnOptions}
                        disabled={isBusy}
                        required
                      />
                    )}
                    {usesNature && (
                      <>
                        <ColumnField
                          form={form}
                          name="natureColumn"
                          options={columnOptions}
                          disabled={isBusy}
                          required
                        />
                        <FormField
                          control={form.control}
                          name="debitValue"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>Texto que significa débito</FormLabel>
                              <FormControl>
                                <Input placeholder="D" disabled={isBusy} {...field} />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <FormField
                          control={form.control}
                          name="creditValue"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>Texto que significa crédito</FormLabel>
                              <FormControl>
                                <Input placeholder="C" disabled={isBusy} {...field} />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      </>
                    )}
                    {usesSeparate && (
                      <>
                        <ColumnField
                          form={form}
                          name="debitColumn"
                          options={columnOptions}
                          disabled={isBusy}
                          required
                        />
                        <ColumnField
                          form={form}
                          name="creditColumn"
                          options={columnOptions}
                          disabled={isBusy}
                          required
                        />
                      </>
                    )}
                  </div>
                )}
              </section>
            </SheetBody>

            {/* Cancelar à ESQUERDA (`justify-between` do SheetFooter). */}
            <SheetFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isBusy}
              >
                Cancelar
              </Button>
              <Button type="submit" disabled={isBusy || !hasColumns}>
                {saveMutation.isPending && (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                )}
                {mapping ? 'Salvar alterações' : 'Salvar mapeamento'}
              </Button>
            </SheetFooter>
          </form>
        </Form>

        {/* Alterar um mapeamento existente: confirmação explícita (R5). Quem
            grava é o caller depois do OK; o diálogo não fecha sozinho. */}
        {mapping !== null && (
          <AlertDialog open={confirmOpen} onOpenChange={(next) => !isBusy && setConfirmOpen(next)}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Alterar o mapeamento deste cliente?</AlertDialogTitle>
                <AlertDialogDescription>
                  O mapeamento novo substitui o atual e altera como os próximos arquivos serão
                  lidos. Os arquivos já processados não mudam.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel disabled={isBusy}>Cancelar</AlertDialogCancel>
                <AlertDialogAction
                  variant="default"
                  disabled={isBusy}
                  onClick={() => void persist(form.getValues())}
                >
                  {saveMutation.isPending && (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  )}
                  Confirmar alteração
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        )}
      </SheetContent>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------
// Peças
// ---------------------------------------------------------------------------

function ColumnField({
  form,
  name,
  options,
  disabled,
  required = false,
  description,
}: {
  form: ReturnType<typeof useForm<InputMappingFormValues>>;
  name: MappingColumnField;
  options: readonly string[];
  disabled: boolean;
  required?: boolean;
  description?: string;
}) {
  const label = MAPPING_FIELD_LABELS[name];
  return (
    <FormField
      control={form.control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel>
            {label}
            {!required && <span className="text-muted-foreground font-normal"> (opcional)</span>}
          </FormLabel>
          <Select
            value={field.value === '' ? (required ? undefined : NONE) : field.value}
            onValueChange={(v) => field.onChange(v === NONE ? '' : v)}
            disabled={disabled || options.length === 0}
          >
            <FormControl>
              <SelectTrigger aria-label={`Coluna: ${label}`}>
                <SelectValue placeholder="Escolha a coluna" />
              </SelectTrigger>
            </FormControl>
            <SelectContent>
              {!required && <SelectItem value={NONE}>Nenhuma</SelectItem>}
              {options.map((column) => (
                <SelectItem key={column} value={column}>
                  {column}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {description !== undefined && <FormDescription>{description}</FormDescription>}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

/**
 * As três convenções como um grupo de rádio NATIVO (APG: `radiogroup` com
 * `radio`s rotulados). Sem opção pré-marcada de propósito — o PRD proíbe
 * inferir sinal, e um padrão seria inferência com outro nome.
 */
function SignConventionGroup({
  value,
  onChange,
  disabled,
}: {
  value: SignConvention | '';
  onChange: (value: SignConvention) => void;
  disabled: boolean;
}) {
  return (
    <div
      role="radiogroup"
      aria-label="Convenção de sinal"
      className="grid gap-2"
      data-testid="sign-convention-group"
    >
      {SIGN_CONVENTIONS.map((convention) => {
        const id = `sign-convention-${convention}`;
        const checked = value === convention;
        return (
          <label
            key={convention}
            htmlFor={id}
            className={
              checked
                ? 'border-primary bg-accent text-accent-foreground flex cursor-pointer items-start gap-3 rounded-md border p-3 text-sm'
                : 'border-input hover:bg-accent hover:text-accent-foreground flex cursor-pointer items-start gap-3 rounded-md border p-3 text-sm'
            }
          >
            <input
              id={id}
              type="radio"
              name="signConvention"
              value={convention}
              checked={checked}
              disabled={disabled}
              onChange={() => onChange(convention)}
              className="accent-primary mt-0.5 h-4 w-4 cursor-pointer"
            />
            <span className="space-y-0.5">
              <span className="block font-medium">{SIGN_CONVENTION_LABELS[convention]}</span>
              <span className="text-muted-foreground block text-xs">
                {SIGN_CONVENTION_DESCRIPTIONS[convention]}
              </span>
            </span>
          </label>
        );
      })}
    </div>
  );
}

/** Colunas encontradas + amostra das primeiras linhas, como o servidor devolveu. */
function InspectionSample({ inspection }: { inspection: FileInspectResult }) {
  return (
    <div className="space-y-2" data-testid="inspection-sample">
      <p className="text-sm">
        <span className="font-medium">{inspection.columns.length}</span>{' '}
        {inspection.columns.length === 1 ? 'coluna encontrada' : 'colunas encontradas'} em um
        arquivo {FILE_FORMAT_LABELS[inspection.format]}.
        {inspection.hasMapping && ' Este cliente já tem mapeamento salvo.'}
      </p>
      {/* Altura própria: a gaveta rola no `SheetBody`; a amostra rola por dentro. */}
      <TableCard className="max-h-64">
        <Table fill scrollRegionLabel="Amostra das primeiras linhas do arquivo (rolável)">
          <TableHeader>
            <TableRow>
              {inspection.columns.map((column, index) => (
                <TableHead key={`${index}:${column}`} className="whitespace-nowrap">
                  {column}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {inspection.sample.map((row, rowIndex) => (
              <TableRow key={rowIndex}>
                {inspection.columns.map((column, cellIndex) => (
                  <TableCell key={`${rowIndex}:${cellIndex}`} className="whitespace-nowrap">
                    {row[cellIndex] ?? ''}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableCard>
    </div>
  );
}
