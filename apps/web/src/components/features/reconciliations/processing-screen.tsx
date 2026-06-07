'use client';

/**
 * Tela de progresso do processamento — `[FRONT 8.7]`, Doc §13.1.
 *
 * Faz polling em `GET /api/v1/reconciliations/{id}/status` (cadência 3s,
 * configurada em `useSessionStatus`) e renderiza o ciclo de vida da sessão
 * em 4 steps visuais. Quando a sessão sai do `processing`, transita para:
 *   - `reviewing`/`done` →
 *     `router.replace('/clientes/{clientId}/conciliacao/{sessionId}')`
 *     (tela de revisão entregue em S11).
 *   - `error` → renderiza alert vermelho com a `error_message` da sessão
 *     e CTAs para voltar ao formulário ou ao detalhe do cliente.
 *
 * Decisões:
 *   - **Steps por tempo decorrido:** o backend ainda não emite sub-status
 *     no `/status` (só `processing|reviewing|done|error`). Como UX simulada,
 *     usamos o tempo desde a montagem da tela como proxy do step ativo
 *     (0–2s: salvando, 2–10s: Omie, 10s+: cruzamento). Quando S11+ trouxer
 *     SSE/sub-status, o front migra sem mudar a forma da tela.
 *   - **Timeout de 5 min:** se a sessão ficar em `processing` por mais de
 *     5 minutos, mostramos um alert âmbar + paramos o polling (o pitfall
 *     §6 do briefing — `enabled: false` em `useSessionStatus`). O usuário
 *     pode "Atualizar" (refetch manual + reset do timer) ou desistir.
 *   - **`router.replace` vs `push` no redirect de reviewing:** `replace` pra
 *     que o botão "voltar" do navegador NÃO volte para esta tela (que faria
 *     polling de uma sessão já concluída — pitfall §2 do briefing).
 *   - **Erros de rede no /status** não derrubam os steps: o TanStack Query
 *     mantém `data` anterior; o usuário só percebe se um erro de fundo
 *     persistir. Disparamos um toast informativo quando `isError` aparece
 *     pela primeira vez, mas mantemos a tela renderizando.
 */

import { AlertTriangle, ArrowLeft, Check, Loader2, RefreshCw, XOctagon } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { useSessionStatus } from '@/hooks/use-reconciliations';
import { ApiError } from '@/lib/api/client';
import { cn } from '@/lib/utils';

/** 15 minutos em ms — alinhado com `WorkerSettings.job_timeout=900s` do
 *  ARQ. Em extratos reais (11+ pares) a qualificação semântica (Anthropic
 *  por par) regularmente passa de 5min; antes a tela mostrava "demorando
 *  demais" enquanto o backend ainda estava trabalhando normalmente.
 *  Const explícita pra ser trivial reduzir em dev/teste manual. */
const TIMEOUT_MS = 15 * 60 * 1000;

/** Thresholds (em ms desde a montagem) que ativam cada step de proxy. */
const STEP_THRESHOLDS_MS = {
  fetchingOmie: 2_000,
  matching: 10_000,
} as const;

interface ProcessingScreenProps {
  clientId: string;
  sessionId: string;
}

type StepIndex = 0 | 1 | 2 | 3;
type StepState = 'pending' | 'active' | 'done';

interface StepDef {
  label: string;
}

const STEPS: readonly StepDef[] = [
  { label: 'Salvando dados' },
  { label: 'Buscando lançamentos Omie' },
  { label: 'Cruzando movimentações' },
  { label: 'Pronto' },
];

export function ProcessingScreen({ clientId, sessionId }: ProcessingScreenProps) {
  const router = useRouter();
  const [timedOut, setTimedOut] = useState(false);
  const [elapsedMs, setElapsedMs] = useState(0);
  const startedAtRef = useRef<number>(Date.now());
  // Flag pra disparar o toast de "rede instável" só uma vez por sessão de
  // fetch — sem isso, cada poll que falha gera um toast novo.
  const hasReportedNetworkErrorRef = useRef(false);

  const statusQuery = useSessionStatus(sessionId, { enabled: !timedOut });

  // Tick interno que avança os steps por tempo decorrido. Granularidade de
  // 500 ms é suficiente — os steps mudam em segundos, não em frames.
  useEffect(() => {
    const tick = setInterval(() => {
      setElapsedMs(Date.now() - startedAtRef.current);
    }, 500);
    return () => clearInterval(tick);
  }, []);

  // Timer de timeout (15 min — ver TIMEOUT_MS). Independente do tick acima
  // pra não rearmar a cada update do `elapsedMs`. Roda uma vez na montagem.
  useEffect(() => {
    const t = setTimeout(() => setTimedOut(true), TIMEOUT_MS);
    return () => clearTimeout(t);
  }, []);

  // Redirect automático quando processamento termina com sucesso. `replace`
  // remove a tela de processando do histórico do navegador — pitfall §2 do
  // briefing da S10 (sem isso, o botão "voltar" voltaria pra cá com polling
  // de uma sessão já concluída).
  useEffect(() => {
    const status = statusQuery.data?.status;
    if (status === 'reviewing' || status === 'done') {
      router.replace(`/clientes/${clientId}/conciliacao/${sessionId}`);
    }
  }, [statusQuery.data?.status, router, clientId, sessionId]);

  // Toast informativo em erro de fetch (rede caiu, 5xx, etc). NÃO desmonta
  // a tela — o último `data` segue válido. Reset da flag quando o próximo
  // poll volta a ser bem-sucedido.
  useEffect(() => {
    if (statusQuery.isError && !hasReportedNetworkErrorRef.current) {
      hasReportedNetworkErrorRef.current = true;
      const err = statusQuery.error;
      const message =
        err instanceof ApiError
          ? err.userMessage
          : 'Falha ao consultar o status. Tentando novamente em segundos.';
      toast.error(message);
    }
    if (!statusQuery.isError) {
      hasReportedNetworkErrorRef.current = false;
    }
  }, [statusQuery.isError, statusQuery.error]);

  function handleRefresh() {
    // Reseta o timeout e refaz o fetch. `enabled` volta pra true porque
    // `timedOut` virou false; o `useSessionStatus` retoma o polling.
    startedAtRef.current = Date.now();
    setElapsedMs(0);
    setTimedOut(false);
    void statusQuery.refetch();
  }

  function handleBackToForm() {
    router.push(`/clientes/${clientId}/conciliacao/nova`);
  }

  function handleBackToClient() {
    router.push(`/clientes/${clientId}`);
  }

  const status = statusQuery.data?.status;
  const isErrorState = status === 'error';

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Processando conciliação</h1>
        <p className="text-muted-foreground text-sm">
          Aguarde enquanto buscamos os lançamentos no Omie, cruzamos com as movimentações do arquivo
          e qualificamos cada par com IA. Isso pode levar até 15 minutos.
        </p>
      </header>

      {isErrorState && (
        <ErrorAlert
          message={normalizeErrorMessage(statusQuery.data?.error_message ?? null)}
          onBackToForm={handleBackToForm}
          onBackToClient={handleBackToClient}
        />
      )}

      {!isErrorState && timedOut && (
        <TimeoutAlert onRefresh={handleRefresh} onBackToForm={handleBackToForm} />
      )}

      {!isErrorState && !timedOut && <StepList activeStep={resolveActiveStep(elapsedMs, status)} />}
    </div>
  );
}

/**
 * Mapeia (tempo decorrido, status) → índice do step ativo.
 *
 * Quando o backend devolver `reviewing`/`done`, ainda passamos brevemente
 * pelo step 4 (`Pronto`) antes do `router.replace` resolver. Isso evita
 * o flash de tela em branco entre o fetch e a navegação.
 */
function resolveActiveStep(elapsedMs: number, status: string | undefined): StepIndex {
  if (status === 'reviewing' || status === 'done') return 3;
  if (elapsedMs >= STEP_THRESHOLDS_MS.matching) return 2;
  if (elapsedMs >= STEP_THRESHOLDS_MS.fetchingOmie) return 1;
  return 0;
}

interface StepListProps {
  activeStep: StepIndex;
}

function StepList({ activeStep }: StepListProps) {
  return (
    <ol
      // `aria-live="polite"` no container: leitores de tela anunciam quando
      // um step muda de "ativo" para "concluído", sem interromper a fala.
      aria-live="polite"
      className="bg-card divide-border space-y-0 divide-y rounded-lg border"
    >
      {STEPS.map((step, idx) => {
        const state: StepState =
          idx < activeStep ? 'done' : idx === activeStep ? 'active' : 'pending';
        return <StepRow key={step.label} index={idx} label={step.label} state={state} />;
      })}
    </ol>
  );
}

interface StepRowProps {
  index: number;
  label: string;
  state: StepState;
}

function StepRow({ index, label, state }: StepRowProps) {
  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <StepIcon state={state} index={index} />
      <span
        className={cn(
          'text-sm',
          state === 'done' && 'text-foreground',
          state === 'active' && 'text-foreground font-medium',
          state === 'pending' && 'text-muted-foreground',
        )}
      >
        {label}
      </span>
      <span className="sr-only">
        {state === 'done' && '— concluído'}
        {state === 'active' && '— em andamento'}
        {state === 'pending' && '— pendente'}
      </span>
    </li>
  );
}

function StepIcon({ state, index }: { state: StepState; index: number }) {
  if (state === 'done') {
    return (
      <span className="flex h-7 w-7 items-center justify-center rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">
        <Check className="h-4 w-4" aria-hidden="true" />
      </span>
    );
  }
  if (state === 'active') {
    return (
      <span className="text-primary flex h-7 w-7 items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
      </span>
    );
  }
  return (
    <span className="bg-muted text-muted-foreground flex h-7 w-7 items-center justify-center rounded-full text-xs font-medium">
      {index + 1}
    </span>
  );
}

interface ErrorAlertProps {
  message: string;
  onBackToForm: () => void;
  onBackToClient: () => void;
}

function ErrorAlert({ message, onBackToForm, onBackToClient }: ErrorAlertProps) {
  return (
    <section
      role="alert"
      className="bg-destructive/5 border-destructive/30 text-destructive space-y-3 rounded-lg border p-4"
    >
      <div className="flex items-start gap-3">
        <XOctagon className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
        <div className="space-y-1">
          <p className="text-sm font-semibold">Não foi possível processar</p>
          <p className="text-sm leading-snug">{message}</p>
        </div>
      </div>
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button variant="outline" size="sm" onClick={onBackToClient}>
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          Voltar para o cliente
        </Button>
        <Button size="sm" onClick={onBackToForm}>
          Voltar ao formulário
        </Button>
      </div>
    </section>
  );
}

interface TimeoutAlertProps {
  onRefresh: () => void;
  onBackToForm: () => void;
}

function TimeoutAlert({ onRefresh, onBackToForm }: TimeoutAlertProps) {
  return (
    <section
      role="status"
      // Âmbar — não é erro fatal; o backend pode ainda concluir.
      className="space-y-3 rounded-lg border border-amber-300 bg-amber-50 p-4 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
        <div className="space-y-1">
          <p className="text-sm font-semibold">
            O processamento está demorando mais que o esperado
          </p>
          <p className="text-sm leading-snug">
            Atualize para verificar o resultado, ou volte ao formulário para iniciar uma nova
            conciliação.
          </p>
        </div>
      </div>
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button variant="outline" size="sm" onClick={onBackToForm}>
          Voltar ao formulário
        </Button>
        <Button size="sm" onClick={onRefresh}>
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
          Atualizar
        </Button>
      </div>
    </section>
  );
}

/**
 * `error_message` chega como `string | null` do back. Tratamos `null` E
 * string vazia como "sem mensagem específica" — pitfall §5 do briefing.
 */
function normalizeErrorMessage(raw: string | null): string {
  const trimmed = raw?.trim();
  if (trimmed === undefined || trimmed === '') {
    return 'Erro inesperado durante o processamento.';
  }
  return trimmed;
}
