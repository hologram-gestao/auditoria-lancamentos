/**
 * Hash SHA-256 de arquivos no browser via Web Crypto API.
 *
 * Uso: `[FRONT 6.1]` calcula o hash do extrato/fatura antes de chamar
 * `/check-duplicate`. O hash trafega pelo back; o arquivo permanece no
 * cliente até S9 (parsing).
 *
 * Decisões:
 *   - `crypto.subtle.digest` aceita o `ArrayBuffer` inteiro de uma vez —
 *     com o teto de 20 MB do form é seguro carregar tudo em memória, e
 *     evita o overhead de chunking que só rende em arquivos GB-sized.
 *   - Saída em hex **lowercase** porque o back armazena o hash lowercase
 *     em DB e a comparação de igualdade é case-sensitive (apesar do regex
 *     do endpoint aceitar `[a-fA-F0-9]`).
 *   - `crypto.subtle` só está disponível em contextos seguros (HTTPS ou
 *     localhost). Em dev (`localhost:3000`) funciona; staging/prod sem TLS
 *     quebra. Cobrimos com um throw explícito caso `crypto.subtle` esteja
 *     indefinido — mais útil que `TypeError` genérico.
 */

export async function sha256Hex(file: File): Promise<string> {
  if (typeof crypto === 'undefined' || crypto.subtle === undefined) {
    throw new Error(
      'Web Crypto API indisponível neste contexto. ' +
        'O cálculo de hash exige HTTPS (ou localhost em desenvolvimento).',
    );
  }

  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest('SHA-256', buffer);
  return bufferToHex(digest);
}

function bufferToHex(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}
