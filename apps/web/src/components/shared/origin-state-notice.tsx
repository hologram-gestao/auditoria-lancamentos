'use client';

/**
 * Ausência ou falha de origem é ESTADO, não erro (Sprint 9 / R7).
 *
 * Um componente só para as telas que dependem da origem (contas, conciliações,
 * revisão, exportação). Ele recebe o `error` cru do TanStack Query, pergunta ao
 * `lib/origin-state` se aquilo é um dos TRÊS códigos da taxonomia e, se for,
 * renderiza o estado explicativo **com o caminho de saída**. Se não for, devolve
 * `null` — e o caller degrada como sempre degradou (toast/`ErrorState`).
 *
 * É por isso que ele não é um `ErrorState` com texto diferente: o caller precisa
 * saber se tratou ou não, e a resposta vem do próprio componente ser nulo.
 *
 * **Copy distinta por código** (R7): "sem origem" manda conectar, "origem com
 * erro" manda reconectar, "capacidade ausente" não manda fazer nada — não há o
 * que consertar. Trocar um pelo outro é o defeito que o PRD nomeia: mandar o
 * parceiro criar uma conexão que já existe.
 *
 * **A ação respeita a permissão do R5.** Sem `manage_client_connections` o
 * usuário vê o estado e a explicação, mas nenhum botão — não é barreira
 * (o backend é), é não oferecer o que o servidor nega (§4.9).
 */

import { PlugZap, Unplug, Wrench } from 'lucide-react';
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { hasPermission } from '@/lib/authz';
import {
  ORIGIN_ERROR_COPY,
  originErrorCode,
  originFixPath,
  type OriginErrorCode,
} from '@/lib/origin-state';
import { useAuthStore } from '@/stores/auth';

const ICON: Record<OriginErrorCode, typeof Unplug> = {
  SEM_CONEXAO: Unplug,
  ORIGEM_COM_ERRO: Wrench,
  CAPACIDADE_AUSENTE: PlugZap,
};

interface OriginStateNoticeProps {
  /** O `error` do TanStack Query, cru. Qualquer coisa que não seja um dos três códigos → `null`. */
  error: unknown;
  clientId: string;
  /**
   * Quando o estado aparece DENTRO de uma gaveta/modal que já leva ao painel,
   * o link seria uma armadilha (navegar fecharia o contexto). Nesses casos a
   * tela passa `false` e fica só a explicação.
   */
  showAction?: boolean;
}

export function OriginStateNotice({
  error,
  clientId,
  showAction = true,
}: OriginStateNoticeProps): React.ReactElement | null {
  const code = originErrorCode(error);
  if (code === null) return null;
  return <OriginStateBlock code={code} clientId={clientId} showAction={showAction} />;
}

interface OriginStateBlockProps {
  code: OriginErrorCode;
  clientId: string;
  showAction?: boolean;
  /**
   * `block` (padrão) é a caixa vazia que substitui uma lista. `inline` é a
   * mesma informação numa linha, para onde a caixa não cabe — ao lado de um
   * botão num cabeçalho, por exemplo. A COPY é a mesma nos dois: variar o
   * texto por lugar recriaria a divergência que este módulo existe para evitar.
   */
  variant?: 'block' | 'inline';
}

/**
 * A mesma caixa, a partir do CÓDIGO em vez do erro.
 *
 * Existe para a tela que já sabe o estado sem ter chamado nada — as contas, por
 * exemplo, sabem pelo `origin_status` do detalhe que o sync responderia 409, e
 * oferecer "Extrair contas" ali seria oferecer o que o servidor nega (§4.9).
 * Fabricar um `ApiError` só para reusar o componente seria mentir sobre ter
 * havido uma resposta.
 */
export function OriginStateBlock({
  code,
  clientId,
  showAction = true,
  variant = 'block',
}: OriginStateBlockProps): React.ReactElement {
  const currentUser = useAuthStore((s) => s.user);
  const copy = ORIGIN_ERROR_COPY[code];
  const Icon = ICON[code];
  // `CAPACIDADE_AUSENTE` não tem caminho de saída (`actionLabel: null`) — e sem
  // a permissão do R5 nenhum dos três oferece ação.
  const canConnect = hasPermission(currentUser, 'manage_client_connections');
  const showLink = showAction && copy.actionLabel !== null && canConnect;

  if (variant === 'inline') {
    return (
      <div
        role="status"
        data-origin-state={code}
        className="text-muted-foreground flex flex-wrap items-center gap-2 text-sm"
      >
        <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span>{copy.title}.</span>
        {showLink && (
          <Link
            href={originFixPath(clientId)}
            className="text-foreground underline underline-offset-4"
          >
            {copy.actionLabel}
          </Link>
        )}
      </div>
    );
  }

  return (
    <div
      // `status` e não `alert`: é uma etapa de configuração que falta, não uma
      // falha — `alert` interrompe o leitor de tela como se algo tivesse dado
      // errado. O `role` também é o que os testes usam para achar o estado.
      role="status"
      data-origin-state={code}
      className="bg-card flex flex-col items-center gap-4 rounded-lg border border-dashed p-8 text-center"
    >
      <Icon className="text-muted-foreground h-8 w-8" aria-hidden="true" />
      <div className="space-y-1.5">
        <p className="text-sm font-medium">{copy.title}</p>
        <p className="text-muted-foreground text-sm">{copy.description}</p>
      </div>
      {showLink && (
        <Button asChild variant="outline">
          <Link href={originFixPath(clientId)}>{copy.actionLabel}</Link>
        </Button>
      )}
    </div>
  );
}
