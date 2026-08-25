/**
 * Badge do status Omie (Conciliado / Atrasado / Previsto / outros).
 * Cores conforme Doc §14.4 (Atrasado vermelho, Previsto amarelo).
 */
interface OmieStatusBadgeProps {
  status: string;
}

export function OmieStatusBadge({ status }: OmieStatusBadgeProps) {
  const lower = status.toLowerCase();
  if (lower === 'atrasado') {
    return (
      <span className="bg-destructive-muted text-destructive inline-flex rounded-full px-2 py-0.5 text-xs font-medium">
        Atrasado
      </span>
    );
  }
  if (lower === 'previsto') {
    return (
      <span className="bg-warning-muted text-warning inline-flex rounded-full px-2 py-0.5 text-xs font-medium">
        Previsto
      </span>
    );
  }
  if (lower === 'conciliado') {
    return (
      <span className="bg-success-muted text-success inline-flex rounded-full px-2 py-0.5 text-xs font-medium">
        Conciliado
      </span>
    );
  }
  return (
    <span className="bg-muted text-muted-foreground inline-flex rounded-full px-2 py-0.5 text-xs font-medium">
      {status}
    </span>
  );
}
