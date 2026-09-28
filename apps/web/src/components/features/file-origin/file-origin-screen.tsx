'use client';

/**
 * Aba "Origem por arquivo" do cliente — Sprint 14 / R5 (FRONT 14.5 · 14.6).
 *
 * Onde o cliente SEM sistema vira rotina: o mapeamento de colunas é declarado
 * uma vez (14.5) e, do segundo mês em diante, enviar o arquivo é um passo só
 * (14.6). Três seções, nesta ordem, porque cada uma depende da anterior:
 *
 *   1. **Mapeamento de colunas** — resumo do que está salvo, ou o estado vazio
 *      que leva ao editor (só `manage_input_mapping`).
 *   2. **Enviar arquivo** — competência, total opcional e o arquivo; o card
 *      "Será aplicado" e as recusas com motivo específico.
 *   3. **Arquivos processados** — a lista, com autor mascarado pelo servidor.
 *
 * A aba só faz sentido para o cliente que TEM conexão `arquivo` (é assim que o
 * menu a mostra); num deep link sem ela, explica e leva ao painel. A
 * competência do envio mora na URL (`?competence=`): é o parâmetro que o link
 * "Enviar arquivo do mês" do de-para manda.
 *
 * Gating (§4.9): ler é de quem alcança o cliente; enviar é
 * `upload_client_file` (os 5 papéis); configurar é `manage_input_mapping`
 * (todos menos o operador). Nenhum `role ===` aqui — tudo por `lib/authz.ts`.
 */

import { useState } from 'react';

import { fileOriginPath } from '@/components/features/navigation/nav-items';
import { Button } from '@/components/ui/button';
import { useInputMapping } from '@/hooks/use-client-file-origin';
import { useClientDetail } from '@/hooks/use-clients';
import { useUrlState } from '@/hooks/use-url-state';
import { hasPermission } from '@/lib/authz';
import { isCompetence, localCurrentCompetence } from '@/lib/competence';
import type { ClientConnection } from '@/lib/contracts';
import { fileConnectionOf } from '@/lib/origin-capabilities';
import { originFixPath, type OriginErrorCode } from '@/lib/origin-state';
import { useAuthStore } from '@/stores/auth';

import { FILE_IMPORTS_ANCHOR, FileImportsTable } from './file-imports-table';
import { FileUploadSection } from './file-upload-section';
import { InputMappingEditorDrawer } from './input-mapping-editor-drawer';
import { InputMappingSection } from './input-mapping-section';

const PARAM = { competence: 'competence' } as const;

interface EditorState {
  open: boolean;
  /** Arquivo que o envio já tinha em mãos — a gaveta inspeciona ele direto. */
  seedFile: File | null;
  /**
   * O que fazer depois de salvar, quando a gaveta abriu PELO ENVIO: processar o
   * arquivo que já estava escolhido — salvo o mapeamento, o envio prossegue.
   */
  afterSave: (() => void) | null;
  /** Remount por `key` a cada abertura: o formulário nasce limpo. */
  key: number;
}

export function FileOriginScreen({ clientId }: { clientId: string }) {
  const currentUser = useAuthStore((s) => s.user);
  const { get, setMany } = useUrlState();
  // O shell já carregou este detalhe — vem do cache, sem segundo request.
  const clientDetail = useClientDetail(clientId);
  const client = clientDetail.data;
  const connections: readonly ClientConnection[] = client?.connections ?? [];
  const fileConnection = fileConnectionOf(connections);
  const hasFileOrigin = fileConnection !== null;

  const mappingQuery = useInputMapping(clientId, { enabled: hasFileOrigin });
  const [editor, setEditor] = useState<EditorState>({
    open: false,
    seedFile: null,
    afterSave: null,
    key: 0,
  });

  const competenceParam = get(PARAM.competence);
  const competence = isCompetence(competenceParam) ? competenceParam : localCurrentCompetence();

  if (currentUser === null) return null;

  const isClosed = client?.closed_at != null;
  const canManage = hasPermission(currentUser, 'manage_input_mapping') && !isClosed;
  const canUpload = hasPermission(currentUser, 'upload_client_file') && !isClosed;
  // A conexão `arquivo` existe mas não está ativa: o servidor responderia 409
  // `ORIGEM_COM_ERRO` ao envio — o estado explica, a ação não aparece.
  const originCode: OriginErrorCode | null =
    fileConnection !== null && fileConnection.status !== 'ativa' ? 'ORIGEM_COM_ERRO' : null;
  // `null` = sem mapeamento (estado normal); `undefined` = NÃO SABEMOS — carregando,
  // detalhe do cliente ainda sem resposta (query desligada) ou GET que falhou.
  // "Não sabemos" nunca pode virar "não tem": o PUT SUBSTITUI o mapeamento, e
  // tratar a falha como vazio abria o editor de criação sem o AlertDialog.
  const mapping =
    mappingQuery.data !== undefined && !mappingQuery.isError
      ? (mappingQuery.data.mapping ?? null)
      : undefined;
  const mappingLoading = mapping === undefined && !mappingQuery.isError;

  function openEditor(seedFile: File | null = null, afterSave: (() => void) | null = null) {
    if (mapping === undefined) return;
    setEditor((prev) => ({ open: true, seedFile, afterSave, key: prev.key + 1 }));
  }

  return (
    <section aria-labelledby="file-origin-heading" className="flex flex-col gap-4">
      <div className="space-y-1">
        <h2 id="file-origin-heading" className="text-lg font-semibold">
          Origem por arquivo
        </h2>
        <p className="text-muted-foreground text-sm">
          A planilha ou o extrato que este cliente manda todo mês, lido pelo mapeamento de colunas
          dele. As linhas viram a base de movimentos que o de-para classifica.
        </p>
      </div>

      {client !== undefined && !hasFileOrigin ? (
        <NoFileOrigin
          clientId={clientId}
          canConnect={hasPermission(currentUser, 'manage_client_connections') && !isClosed}
        />
      ) : (
        <>
          <InputMappingSection
            mapping={mapping}
            isLoading={mappingLoading}
            isError={mappingQuery.isError}
            error={mappingQuery.error}
            onRetry={() => void mappingQuery.refetch()}
            canManage={canManage}
            isClosed={isClosed}
            onConfigure={() => openEditor(null)}
          />

          <FileUploadSection
            clientId={clientId}
            mapping={mapping}
            mappingFailed={mappingQuery.isError}
            onRetryMapping={() => void mappingQuery.refetch()}
            canUpload={canUpload}
            canManage={canManage}
            isClosed={isClosed}
            originCode={originCode}
            competence={competence}
            onCompetenceChange={(next) =>
              setMany({ [PARAM.competence]: isCompetence(next) ? next : null })
            }
            onConfigureMapping={(file, afterSave) => openEditor(file, afterSave)}
            importsHref={`${fileOriginPath(clientId)}#${FILE_IMPORTS_ANCHOR}`}
          />

          <FileImportsTable clientId={clientId} />

          {/* A gaveta só existe para quem configura E quando se SABE se há
              mapeamento — sem isso "alterar" viraria "criar", sem confirmação.
              Remount por `key` a cada abertura para o estado nascer limpo. */}
          {canManage && mapping !== undefined && (
            <InputMappingEditorDrawer
              key={editor.key}
              open={editor.open}
              onOpenChange={(open) => setEditor((prev) => ({ ...prev, open }))}
              clientId={clientId}
              mapping={mapping}
              seedFile={editor.seedFile}
              onSaved={() => editor.afterSave?.()}
            />
          )}
        </>
      )}
    </section>
  );
}

/**
 * Deep link na aba de um cliente sem conexão `arquivo`: explica e leva ao
 * painel, onde a origem se conecta (mesmo caminho da taxonomia de origem).
 */
function NoFileOrigin({ clientId, canConnect }: { clientId: string; canConnect: boolean }) {
  return (
    <div
      role="status"
      data-testid="no-file-origin"
      className="bg-card flex flex-col items-center gap-4 rounded-lg border border-dashed p-8 text-center"
    >
      <div className="space-y-1.5">
        <p className="text-sm font-medium">Este cliente não tem origem por arquivo</p>
        <p className="text-muted-foreground text-sm">
          Para enviar planilhas ou extratos, conecte uma origem do tipo &quot;Arquivo&quot; no
          painel do cliente.
        </p>
      </div>
      {canConnect && (
        <Button asChild variant="outline">
          <a href={originFixPath(clientId)}>Conectar origem</a>
        </Button>
      )}
    </div>
  );
}
