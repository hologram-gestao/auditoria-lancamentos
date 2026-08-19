'use client';

/**
 * Gaveta "Lançar no Omie" (Sprint 7 / FRONT 07.6 · 07.7).
 *
 * É a última barreira antes de o ADL **gravar na contabilidade do cliente**: o
 * operador vê exatamente quais compras vão, classifica cada uma e confirma.
 * Nunca auto-fire — decisão de segurança do PRD (S-2).
 *
 * Estrutura do design-system (`ui/sheet.tsx`): header fixo · miolo rolável ·
 * footer com **Cancelar à esquerda** e a ação primária à direita.
 *
 * **Duas fases numa gaveta só.** Antes do envio, a gaveta classifica; depois,
 * ela vira o RESUMO por linha. Fechar no sucesso parcial seria esconder
 * justamente o que o operador precisa ler (quem entrou, quem não, por quê) — e
 * é da mesma tela que ele reexecuta as que falharam.
 *
 * **Sem sugestão automática de categoria.** O PRD prevê reusar a qualificação
 * ou o glossário, mas nenhum endpoint da sprint devolve categoria sugerida por
 * linha (conferido no contrato gerado). A task é explícita: sem sugestão real,
 * não se simula sugestão na UI.
 */

import { AlertTriangle, CheckCircle2, Loader2, Upload, XCircle } from 'lucide-react';
import { useMemo, useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Combobox, type ComboboxOption } from '@/components/ui/combobox';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { useOmieCategorias, usePostOmieLancamentos } from '@/hooks/use-omie-postings';
import { ApiError, NetworkError } from '@/lib/api/client';
import type {
  OmiePostingBatchPayload,
  OmiePostingLineRequest,
  OmiePostingLineResult,
} from '@/lib/api/omie-postings';
import type { FileEntryItem } from '@/lib/api/reconciliations';
import { formatBRDate, formatBRL } from '@/lib/format';
import { cn } from '@/lib/utils';

interface LancarNoOmieDrawerProps {
  sessionId: string;
  /** Compras selecionadas na tabela — sempre já filtradas por elegibilidade. */
  entries: FileEntryItem[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * Linhas que o backend confirmou como lançadas (id → `omie_lancamento_id`).
   * A tabela usa para pintar o badge e tirar a compra da seleção.
   */
  onPosted: (results: Record<string, number | null>) => void;
}

export function LancarNoOmieDrawer({
  sessionId,
  entries,
  open,
  onOpenChange,
  onPosted,
}: LancarNoOmieDrawerProps) {
  const postMutation = usePostOmieLancamentos(sessionId);
  // Só busca quando a gaveta abre: no MISS do cache o backend vai ao Omie, e
  // ninguém paga essa ida por abrir a tela de revisão.
  const categoriasQuery = useOmieCategorias(sessionId, { enabled: open });

  /** `cCodCateg` por linha. É ele que decide o que entra no lote (R2). */
  const [categoriaByEntry, setCategoriaByEntry] = useState<Record<string, string>>({});
  /** Categoria do "aplicar a todas" — a da LINHA sempre vence esta. */
  const [categoriaLote, setCategoriaLote] = useState<string | null>(null);
  /** Resumo do último envio. Enquanto `null`, a gaveta está na fase de classificar. */
  const [result, setResult] = useState<OmiePostingBatchPayload | null>(null);

  const opcoes: ComboboxOption[] = useMemo(
    () =>
      (categoriasQuery.data?.data ?? []).map((c) => ({
        value: c.codigo,
        label: `${c.codigo} · ${c.descricao}`,
      })),
    [categoriasQuery.data],
  );

  const resultByEntry = useMemo(() => {
    const map = new Map<string, OmiePostingLineResult>();
    result?.lines.forEach((line) => map.set(line.file_entry_id, line));
    return map;
  }, [result]);

  /**
   * O que seria enviado agora: só as classificadas. Depois de um envio, as que
   * já entraram saem da lista — reenviá-las voltaria "bloqueada: já lançada" e
   * poluiria o resumo com uma linha que o operador não pediu de novo.
   */
  const lines: OmiePostingLineRequest[] = useMemo(
    () =>
      entries
        .filter((entry) => resultByEntry.get(entry.id)?.status !== 'lancada')
        .map((entry) => ({ entry, codigo: categoriaByEntry[entry.id] }))
        .filter(
          (pair): pair is { entry: FileEntryItem; codigo: string } =>
            pair.codigo !== undefined && pair.codigo.trim() !== '',
        )
        .map(({ entry, codigo }) => ({ file_entry_id: entry.id, cod_categoria: codigo })),
    [entries, categoriaByEntry, resultByEntry],
  );

  const pendentes = entries.filter((e) => resultByEntry.get(e.id)?.status !== 'lancada');
  const semCategoria = pendentes.length - lines.length;
  const categoriasIndisponiveis = categoriasQuery.isError;

  function aplicarEmLote(): void {
    if (categoriaLote === null) return;
    setCategoriaByEntry((prev) => {
      const next = { ...prev };
      // Vale para as PENDENTES: reclassificar uma linha já lançada não faria
      // nada além de sugerir que ela pode mudar.
      pendentes.forEach((entry) => {
        next[entry.id] = categoriaLote;
      });
      return next;
    });
  }

  async function handleConfirm(): Promise<void> {
    if (lines.length === 0 || postMutation.isPending) return;
    try {
      const payload = await postMutation.mutateAsync(lines);
      setResult(payload);
      onPosted(collectPosted(payload));
      notifyOutcome(payload);
    } catch (err) {
      // Erro do LOTE inteiro (recurso desligado, sessão que não é de cartão,
      // teto de linhas, Omie fora sem nada lançado). A gaveta continua aberta:
      // fechá-la levaria embora a classificação que o operador acabou de fazer.
      toast.error(resolvePostErrorMessage(err));
    }
  }

  const tudoLancado = result !== null && pendentes.length === 0;

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (!next) setResult(null);
        onOpenChange(next);
      }}
    >
      <SheetContent className="sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>Lançar no Omie</SheetTitle>
          <SheetDescription>
            {entries.length === 1
              ? 'A compra abaixo será lançada na conta do cartão no Omie.'
              : `As ${entries.length} compras abaixo serão lançadas na conta do cartão no Omie.`}{' '}
            O lançamento é gravado na contabilidade do cliente e não pode ser desfeito pelo ADL.
          </SheetDescription>
        </SheetHeader>

        <SheetBody>
          <div className="space-y-4">
            {categoriasIndisponiveis && (
              <div
                role="alert"
                className="bg-destructive-muted text-destructive flex items-start gap-2 rounded-md border p-3 text-sm"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <div className="space-y-2">
                  <p>
                    Não foi possível carregar as categorias do Omie. Sem elas não é possível
                    classificar as compras.
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void categoriasQuery.refetch()}
                    disabled={categoriasQuery.isFetching}
                  >
                    {categoriasQuery.isFetching && (
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    )}
                    Tentar novamente
                  </Button>
                </div>
              </div>
            )}

            {!categoriasIndisponiveis && pendentes.length > 1 && (
              <div className="bg-muted/40 space-y-2 rounded-md border p-3">
                <p className="text-sm font-medium">Classificar todas de uma vez</p>
                <div className="flex flex-wrap items-center gap-2">
                  <div className="min-w-[16rem] flex-1">
                    <Combobox
                      options={opcoes}
                      value={categoriaLote}
                      onValueChange={setCategoriaLote}
                      label="Categoria para todas as compras"
                      placeholder="Escolher categoria…"
                      searchPlaceholder="Buscar categoria…"
                      emptyMessage="Nenhuma categoria encontrada."
                      loading={categoriasQuery.isLoading}
                    />
                  </div>
                  <Button
                    variant="secondary"
                    onClick={aplicarEmLote}
                    disabled={categoriaLote === null || postMutation.isPending}
                  >
                    Aplicar a {pendentes.length} compras
                  </Button>
                </div>
                <p className="text-muted-foreground text-xs">
                  Depois de aplicar, a categoria de cada linha ainda pode ser trocada — a da linha
                  prevalece.
                </p>
              </div>
            )}

            <ul className="divide-border divide-y">
              {entries.map((entry) => {
                const linha = resultByEntry.get(entry.id);
                const categoria = categoriaByEntry[entry.id] ?? null;
                const bloqueadaSemCategoria =
                  linha?.status !== 'lancada' && (categoria === null || categoria.trim() === '');
                return (
                  <li key={entry.id} className="space-y-2 py-3">
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                      <span className="text-muted-foreground w-20 text-sm tabular-nums">
                        {formatBRDate(entry.transaction_date)}
                      </span>
                      <span className="min-w-[8rem] flex-1 text-sm">{entry.description}</span>
                      {/* `ml-auto` + `whitespace-nowrap`: em 390px o valor mais
                          largo não cabe na mesma linha e, sem isto, cai para a
                          linha de baixo ALINHADO À ESQUERDA — enquanto o valor
                          curto da linha vizinha fica à direita. Duas compras do
                          mesmo lote apareciam alinhadas de formas diferentes. */}
                      <span className="ml-auto whitespace-nowrap text-sm tabular-nums">
                        {formatBRL(entry.amount, { signed: true })}
                      </span>
                    </div>

                    {linha === undefined ? (
                      <div className="flex flex-wrap items-center gap-2 pl-20">
                        <div className="min-w-[16rem] flex-1">
                          <Combobox
                            options={opcoes}
                            value={categoria}
                            onValueChange={(codigo) =>
                              setCategoriaByEntry((prev) => ({ ...prev, [entry.id]: codigo }))
                            }
                            label={`Categoria da compra de ${formatBRDate(entry.transaction_date)} — ${entry.description}`}
                            placeholder="Sem categoria"
                            searchPlaceholder="Buscar categoria…"
                            emptyMessage="Nenhuma categoria encontrada."
                            loading={categoriasQuery.isLoading}
                            disabled={categoriasIndisponiveis || postMutation.isPending}
                          />
                        </div>
                        {bloqueadaSemCategoria && (
                          <span className="text-warning inline-flex items-center gap-1 text-xs">
                            <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                            Sem categoria — esta compra não será lançada
                          </span>
                        )}
                      </div>
                    ) : (
                      <LinhaResultado line={linha} />
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        </SheetBody>

        {/* `flex-wrap` só AQUI, não no `SheetFooter` compartilhado: esta é a
            primeira gaveta com TRÊS elementos no rodapé, e o botão primário é
            largo ("Confirmar e lançar N de M"). Quando nem assim couber numa
            linha em 390px, ele desce inteiro para a linha de baixo em vez de
            ser clipado pela borda. */}
        <SheetFooter className="flex-wrap gap-y-3">
          <Button
            variant="outline"
            className="shrink-0"
            onClick={() => onOpenChange(false)}
            disabled={postMutation.isPending}
          >
            {/* "Concluir" e não "Fechar": o X do canto já se chama Fechar, e
                dois controles com o mesmo nome acessível na mesma gaveta são
                ambíguos para quem navega por leitor de tela. */}
            {tudoLancado ? 'Concluir' : 'Cancelar'}
          </Button>
          {/* `min-w-0` + `flex-wrap`: botão é `whitespace-nowrap`, então seu
              min-content é o texto inteiro e ele NÃO encolhe — sem poder ceder
              largura, o grupo empurrava a ação primária para fora da viewport
              em 390px. Com a quebra, o texto auxiliar sobe para a linha de cima
              e o botão continua inteiro, alinhado à direita. */}
          <div className="flex min-w-0 flex-wrap items-center justify-end gap-x-3 gap-y-2">
            {!tudoLancado && semCategoria > 0 && (
              <span className="text-muted-foreground min-w-0 text-xs">
                {semCategoria === 1
                  ? '1 compra fica de fora (sem categoria)'
                  : `${semCategoria} compras ficam de fora (sem categoria)`}
              </span>
            )}
            {!tudoLancado && (
              <Button
                onClick={() => void handleConfirm()}
                disabled={postMutation.isPending || lines.length === 0}
              >
                {postMutation.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    Lançando…
                  </>
                ) : (
                  <>
                    <Upload className="h-4 w-4" aria-hidden="true" />
                    {result === null
                      ? `Confirmar e lançar ${lines.length} de ${pendentes.length}`
                      : `Tentar novamente ${lines.length} de ${pendentes.length}`}
                  </>
                )}
              </Button>
            )}
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

/**
 * Desfecho de UMA linha, com o motivo do backend. O ícone/cor vêm do `status`
 * (enum fechado do contrato) e o texto é a mensagem que o servidor devolveu —
 * em `erro_omie` ela é a frase VERBATIM do provedor, que é o que a torna
 * acionável.
 */
function LinhaResultado({ line }: { line: OmiePostingLineResult }) {
  const { icon: Icon, tone, rotulo } = RESULT_PRESENTATION[line.status];
  return (
    <p className={cn('flex items-start gap-2 pl-20 text-xs', tone)}>
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span>
        <strong className="font-medium">{rotulo}</strong>
        {line.omie_lancamento_id !== null && line.omie_lancamento_id !== undefined && (
          <> · lançamento nº {line.omie_lancamento_id}</>
        )}
        {line.message !== null && line.message !== undefined && <> — {line.message}</>}
      </span>
    </p>
  );
}

/** Um par por status — os três do contrato, sem `default` mágico. */
const RESULT_PRESENTATION = {
  lancada: { icon: CheckCircle2, tone: 'text-success', rotulo: 'Lançada no Omie' },
  bloqueada: { icon: AlertTriangle, tone: 'text-warning', rotulo: 'Não lançada' },
  erro: { icon: XCircle, tone: 'text-destructive', rotulo: 'Erro no lançamento' },
} as const;

/** Linhas com lançamento confirmado — inclui a que já estava lançada. */
function collectPosted(payload: OmiePostingBatchPayload): Record<string, number | null> {
  const out: Record<string, number | null> = {};
  payload.lines.forEach((line) => {
    if (line.status === 'lancada' || line.reason === 'ja_lancada') {
      out[line.file_entry_id] = line.omie_lancamento_id ?? null;
    }
  });
  return out;
}

/**
 * Sucesso verde, parcial em aviso, nada lançado em destrutivo — nunca a cor
 * primária para sucesso, e nunca `richColors` (ADR-017-FE: quem pinta é o
 * `<Toaster>` global, pelos tokens do tema).
 */
function notifyOutcome(payload: OmiePostingBatchPayload): void {
  const falhas = payload.bloqueadas + payload.com_erro;
  if (falhas === 0) {
    toast.success(
      payload.lancadas === 1
        ? '1 compra lançada no Omie.'
        : `${payload.lancadas} compras lançadas no Omie.`,
    );
    return;
  }
  if (payload.lancadas === 0) {
    toast.error('Nenhuma compra foi lançada. Veja o motivo de cada linha.');
    return;
  }
  toast.warning(
    `${payload.lancadas} de ${payload.lines.length} compras lançadas. Veja o motivo das demais.`,
  );
}

/** Reusa o `userMessage` do backend (já em PT-BR) quando existe. */
function resolvePostErrorMessage(err: unknown): string {
  if (err instanceof ApiError || err instanceof NetworkError) return err.userMessage;
  return 'Não foi possível lançar no Omie. Tente novamente em instantes.';
}
