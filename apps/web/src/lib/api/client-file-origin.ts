/**
 * Origem por ARQUIVO do cliente — mapeamento de entrada e ingestão (Sprint 14 /
 * R1 · R2 · R5 — BACK 14.1 · 14.3).
 *
 * Espelha `modules/client_input_mappings/routes.py` e
 * `modules/client_file_ingestion/routes.py`, lidos ANTES de consumir:
 *
 *   - `GET  /clients/{id}/input-mapping`           → `{ data: { mapping } }`; `mapping: null`
 *                                                     é estado NORMAL (sem mapeamento), nunca 404
 *   - `PUT  /clients/{id}/input-mapping`           → cria OU substitui (um por cliente);
 *                                                     `{ data: { mapping, created } }`
 *   - `POST /clients/{id}/file-origin/inspect`     → multipart `file` (+ `csvDelimiter`,
 *                                                     `encoding`); a AMOSTRA só existe na resposta
 *   - `POST /clients/{id}/file-origin/process`     → multipart `file`, `competence`,
 *                                                     `declaredTotal`; 201 com as contagens
 *   - `GET  /clients/{id}/file-origin/imports`     → `{ data: [...] }`, filtro `?competence=`
 *
 * Todos os envelopes têm UMA chave (`{data}`), então o `rawFetch` desembrulha e
 * o caller recebe o miolo. Os nomes dos campos do multipart são os do contrato
 * (`InspectFileBody`/`ProcessFileBody`), conferidos pelo `satisfies` abaixo — o
 * FastAPI lê `declaredTotal` pelo alias, e `declared_total` seria ignorado em
 * silêncio. `client_id` nunca vai no corpo: o tenant é a rota (§3.15).
 *
 * Recusas do arquivo são exceções TIPADAS do servidor (`code` +
 * `userMessage` + `details`), lidas por `lib/file-origin-errors.ts`; forma
 * inválida (competência fora de `YYYY-MM`, total fora do padrão) é o 400
 * genérico — a tela valida esses dois ANTES de enviar.
 */
import type {
  CsvDelimiter,
  FileImportItem,
  FileInspectResult,
  FileProcessResult,
  InputEncoding,
  InputMappingPayload,
  InputMappingRequest,
  InputMappingWritePayload,
  InspectFileBody,
  ProcessFileBody,
} from '@/lib/contracts';

import { apiGet, apiPostMultipart, apiPutJson } from './client';

function clientBase(clientId: string): string {
  return `/api/v1/clients/${encodeURIComponent(clientId)}`;
}

/** Nomes dos campos do multipart — travados no contrato pelo `satisfies`. */
const INSPECT_FIELD = {
  file: 'file',
  csvDelimiter: 'csvDelimiter',
  encoding: 'encoding',
} as const satisfies Record<keyof InspectFileBody, string>;

const PROCESS_FIELD = {
  file: 'file',
  competence: 'competence',
  declaredTotal: 'declaredTotal',
} as const satisfies Record<keyof ProcessFileBody, string>;

// ---------------------------------------------------------------------------
// Mapeamento de entrada (R1)
// ---------------------------------------------------------------------------

/** Leitura liberada a todo papel com acesso ao cliente (o operador vê o resumo). */
export async function getInputMapping(clientId: string): Promise<InputMappingPayload> {
  return apiGet<InputMappingPayload>(`${clientBase(clientId)}/input-mapping`);
}

/**
 * Declara ou SUBSTITUI o mapeamento (`manage_input_mapping`). Forma inválida
 * (convenção ausente ou incoerente com os campos, CSV sem delimitador) é 400
 * genérico; cliente encerrado é 409.
 */
export async function saveInputMapping(
  clientId: string,
  payload: InputMappingRequest,
): Promise<InputMappingWritePayload> {
  return apiPutJson<InputMappingWritePayload>(`${clientBase(clientId)}/input-mapping`, payload);
}

// ---------------------------------------------------------------------------
// Ingestão (R2 · R5)
// ---------------------------------------------------------------------------

export interface InspectFileInput {
  file: File;
  /** Só CSV e só sem mapeamento salvo: DECLARADO, nunca farejado. */
  csvDelimiter?: CsvDelimiter | null;
  encoding?: InputEncoding | null;
}

/**
 * Colunas + amostra das primeiras linhas, para confirmar ou criar o mapeamento.
 * Nada é persistido. Requer `upload_client_file` e conexão `arquivo` ativa.
 */
export async function inspectFile(
  clientId: string,
  input: InspectFileInput,
): Promise<FileInspectResult> {
  const form = new FormData();
  form.append(INSPECT_FIELD.file, input.file);
  if (input.csvDelimiter) form.append(INSPECT_FIELD.csvDelimiter, input.csvDelimiter);
  if (input.encoding) form.append(INSPECT_FIELD.encoding, input.encoding);
  return apiPostMultipart<FileInspectResult>(`${clientBase(clientId)}/file-origin/inspect`, form);
}

export interface ProcessFileInput {
  file: File;
  /** `YYYY-MM` — validado ANTES de enviar (fora do padrão é 400 genérico). */
  competence: string;
  /**
   * Total informado pela pessoa, como decimal `1234.56` (até 2 casas, sinal
   * opcional). Ausente = não conferir. A tela guarda centavos e converte AQUI,
   * na borda — nunca `toFixed` no meio do componente.
   */
  declaredTotal?: string | null;
}

/**
 * Processa o arquivo do mês aplicando o mapeamento salvo. Ou entra INTEIRO, ou
 * nada entra: toda recusa é tipada com o motivo (ver `lib/file-origin-errors`).
 */
export async function processFile(
  clientId: string,
  input: ProcessFileInput,
): Promise<FileProcessResult> {
  const form = new FormData();
  form.append(PROCESS_FIELD.file, input.file);
  form.append(PROCESS_FIELD.competence, input.competence);
  if (input.declaredTotal) form.append(PROCESS_FIELD.declaredTotal, input.declaredTotal);
  return apiPostMultipart<FileProcessResult>(`${clientBase(clientId)}/file-origin/process`, form);
}

/** Os arquivos processados do cliente (todas as competências, ou uma). */
export async function listFileImports(
  clientId: string,
  competence?: string | null,
): Promise<FileImportItem[]> {
  const qs = competence ? `?competence=${encodeURIComponent(competence)}` : '';
  return apiGet<FileImportItem[]>(`${clientBase(clientId)}/file-origin/imports${qs}`);
}
