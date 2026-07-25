'use client';

/**
 * Uma parte (arquivo) no Step 2 da gaveta, com o status individual.
 *
 * O feedback é POR ARQUIVO de propósito: num envio de 3 PDFs em que o 2º falha,
 * "deu erro" no lote inteiro não diz o que fazer. Aqui a pessoa vê qual parte
 * falhou, por quê (mensagem PT-BR do backend, nunca linguagem interna) e pode
 * removê-la sem perder as outras.
 */

import { AlertCircle, CheckCircle2, CopyX, FileText, Loader2, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { formatBRL } from '@/lib/format';
import { cn } from '@/lib/utils';

import { formatFileSize } from '../file-input-field';

import type { UploadItem, UploadStatus } from './use-file-pipeline';

const RUNNING_LABEL: Partial<Record<UploadStatus, string>> = {
  queued: 'Na fila…',
  hashing: 'Calculando assinatura…',
  checking: 'Verificando duplicata…',
  parsing: 'Extraindo movimentações com IA…',
};

interface UploadItemRowProps {
  item: UploadItem;
  onRemove: () => void;
  disabled: boolean;
}

export function UploadItemRow({ item, onRemove, disabled }: UploadItemRowProps) {
  const running = RUNNING_LABEL[item.status];
  const isParsed = item.status === 'parsed';
  const isDuplicate = item.status === 'duplicate';
  const isFailed = item.status === 'error' || item.status === 'invalid';

  return (
    <li
      className={cn(
        'flex items-start gap-3 rounded-md border p-3 text-sm',
        isFailed && 'border-destructive/40 bg-destructive/5',
        isDuplicate && 'border-warning/40 bg-warning-muted',
      )}
    >
      <StatusIcon status={item.status} />

      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="truncate font-medium" title={item.file.name}>
          {item.file.name}
        </p>
        <p className="text-muted-foreground text-xs">{formatFileSize(item.file.size)}</p>

        {running !== undefined && (
          <p className="text-muted-foreground text-xs" role="status">
            {running}
          </p>
        )}
        {isParsed && item.result !== undefined && (
          <>
            <p className="text-muted-foreground text-xs">
              {item.result.statement.transactions.length} movimentaç
              {item.result.statement.transactions.length === 1 ? 'ão' : 'ões'} extraída
              {item.result.statement.transactions.length === 1 ? '' : 's'}
            </p>
            {/* Checksum de saldos (BACK 02.3). AVISO, não bloqueio: numa fatura
                quebrada em partes, a identidade de saldo só fecha no CONJUNTO —
                barrar parte a parte impediria justamente o caso de uso do
                multi-arquivo. O veredito do conjunto sai no detalhe (R3). */}
            {item.result.checksum.applicable && !item.result.checksum.ok && (
              <p className="text-warning-foreground text-xs">
                Os saldos deste arquivo não fecham (diferença de{' '}
                {formatBRL(item.result.checksum.difference)}). A conciliação segue, mas confira o
                resumo depois.
              </p>
            )}
          </>
        )}
        {item.errorMessage !== undefined && (
          <p className={cn('text-xs', isDuplicate ? 'text-warning-foreground' : 'text-destructive')}>
            {item.errorMessage}
            {item.errorCode !== undefined && ` (cód. ${item.errorCode})`}
          </p>
        )}
      </div>

      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={onRemove}
        disabled={disabled}
        aria-label={`Remover ${item.file.name}`}
      >
        <X className="h-4 w-4" aria-hidden="true" />
      </Button>
    </li>
  );
}

function StatusIcon({ status }: { status: UploadStatus }) {
  switch (status) {
    case 'parsed':
      return <CheckCircle2 className="text-success mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />;
    case 'duplicate':
      return <CopyX className="text-warning mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />;
    case 'error':
    case 'invalid':
      return <AlertCircle className="text-destructive mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />;
    case 'queued':
      return <FileText className="text-muted-foreground mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />;
    default:
      return (
        <Loader2
          className="text-muted-foreground mt-0.5 h-4 w-4 shrink-0 animate-spin"
          aria-hidden="true"
        />
      );
  }
}
