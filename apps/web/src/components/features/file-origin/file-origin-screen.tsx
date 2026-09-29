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
 * A aba é listada para TODO cliente desde o follow-up 86e3fqnc9 — antes ela só
 * existia para quem já tinha conexão `arquivo`, e o recurso ficava invisível
 * para quem precisava descobri-lo. Quem explica o estado é esta tela
 * (`NoFileOrigin`), em três casos distintos: sem origem nenhuma (dá para
 * conectar), origem de OUTRO tipo já conectada (não dá — um cliente, um tipo de
 * origem de lançamentos, §4.8) e cliente encerrado. A competência do envio mora
 * na URL (`?competence=`): é o parâmetro que o link "Enviar arquivo do mês" do
 * de-para manda.
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
import { DEFAULT_PROVIDER_LABEL, FILE_PROVIDER_TYPE } from '@/lib/api/client-connections';
import { hasPermission } from '@/lib/authz';
import { isCompetence, localCurrentCompetence } from '@/lib/competence';
import type { ClientConnection } from '@/lib/contracts';
import { fileConnectionOf, selectCapableConnection } from '@/lib/origin-capabilities';
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
        <h1 id="file-origin-heading" className="text-xl font-semibold">
          Origem por arquivo
        </h1>
        <p className="text-muted-foreground text-sm">
          A planilha ou o extrato que este cliente manda todo mês, lido pelo mapeamento de colunas
          dele. As linhas viram a base de movimentos que o de-para classifica.
        </p>
      </div>

      {client !== undefined && !hasFileOrigin ? (
        <NoFileOrigin
          clientId={clientId}
          isClosed={isClosed}
          // A origem que JÁ lista lançamentos neste cliente, se houver: com ela
          // conectar arquivo é 409 `ORIGEM_JA_CONECTADA` (§4.8), então a tela
          // explica em vez de oferecer. Pela CAPACIDADE, nunca por
          // `provider_type === 'omie'` — o terceiro provedor entra sem tocar aqui.
          ledgerConnection={selectCapableConnection(connections, 'listar_lancamentos')}
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
 * O cliente não tem conexão `arquivo`: a aba existe para TODOS (86e3fqnc9),
 * então este estado é o que a maioria dos clientes vê. Ele tem duas obrigações:
 * dizer o que é a origem por arquivo — é assim que quem opera descobre que dá
 * para atender cliente sem ERP — e dizer por que ela não está disponível AQUI,
 * com a ação certa para o papel de quem lê.
 *
 * Três casos, e a ordem importa: encerrado vence tudo (toda escrita é 409,
 * §4.12); origem que já lista lançamentos vem depois (conectar seria 409
 * `ORIGEM_JA_CONECTADA`, então não há botão — §4.9); sobra o cliente sem
 * origem, o único onde conectar faz sentido.
 */
function NoFileOrigin({
  clientId,
  isClosed,
  ledgerConnection,
  canConnect,
}: {
  clientId: string;
  isClosed: boolean;
  ledgerConnection: ClientConnection | null;
  canConnect: boolean;
}) {
  const state = isClosed ? 'encerrado' : ledgerConnection !== null ? 'outra-origem' : 'sem-origem';
  const ledgerLabel =
    ledgerConnection === null
      ? ''
      : (DEFAULT_PROVIDER_LABEL[ledgerConnection.provider_type] ?? ledgerConnection.provider_type);
  return (
    <div
      role="status"
      data-testid="no-file-origin"
      data-state={state}
      className="bg-card flex flex-col items-center gap-4 rounded-lg border border-dashed p-8 text-center"
    >
      <div className="mx-auto max-w-prose space-y-1.5">
        <p className="text-sm font-medium">
          {state === 'encerrado'
            ? 'Este cliente foi encerrado'
            : state === 'outra-origem'
              ? `Os lançamentos deste cliente vêm da origem ${ledgerLabel}`
              : 'Este cliente ainda não recebe arquivos'}
        </p>
        <p className="text-muted-foreground text-sm">
          A origem por arquivo atende o cliente que não tem sistema contábil: a planilha ou o
          extrato do mês é lido por um mapeamento de colunas configurado uma vez e vira a base de
          movimentos que o de-para classifica.
        </p>
        <p className="text-muted-foreground text-sm">
          {state === 'encerrado'
            ? 'Cliente encerrado é só leitura: não é possível conectar uma origem nem enviar arquivos.'
            : state === 'outra-origem'
              ? `Cada cliente tem um tipo de origem de lançamentos só. Para passar a receber arquivos, a conexão ${ledgerLabel} precisa ser removida antes, no painel do cliente.`
              : canConnect
                ? 'Para começar, conecte uma origem do tipo "Arquivo".'
                : 'Para começar, uma origem do tipo "Arquivo" precisa ser conectada. Peça ao administrador ou ao gerente responsável pela conta.'}
        </p>
      </div>
      {state === 'sem-origem' && canConnect && (
        <Button asChild variant="outline">
          {/* Leva ao painel com a gaveta já aberta no tipo Arquivo: quem clica
              aqui está justamente atrás dessa origem. */}
          <a href={originFixPath(clientId, FILE_PROVIDER_TYPE)}>Conectar origem por arquivo</a>
        </Button>
      )}
      {state === 'outra-origem' && (
        <Button asChild variant="outline">
          <a href={originFixPath(clientId)}>Ver origem do cliente</a>
        </Button>
      )}
    </div>
  );
}
