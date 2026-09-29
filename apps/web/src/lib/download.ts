/**
 * Dispara o download de um blob no navegador — o padrão da casa, num lugar só
 * (antes copiado em `export-report-button.tsx` e `client-mapping-screen.tsx`).
 *
 * Link temporário + `click()`; o `revokeObjectURL` no fim libera a memória
 * (Chrome e Firefox seguram a referência indefinidamente sem ele).
 */
export function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
