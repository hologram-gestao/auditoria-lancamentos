/**
 * Ícone da landing em pastilha com o gradiente da marca (86e3fr9vz). O texto de
 * `content.ts` diz QUAL ícone por uma chave; o mapa para o `lucide-react` mora aqui.
 * Decorativo: o título ao lado já diz tudo.
 */
import {
  Briefcase,
  Building2,
  Calculator,
  CalendarClock,
  FileSpreadsheet,
  FileX2,
  KeyRound,
  Lock,
  ShieldAlert,
  Table,
  UsersRound,
  type LucideIcon,
} from 'lucide-react';

const ICONS = {
  calculator: Calculator,
  briefcase: Briefcase,
  building: Building2,
  key: KeyRound,
  file: FileX2,
  users: UsersRound,
  lock: Lock,
  'calendar-clock': CalendarClock,
  'shield-alert': ShieldAlert,
  table: Table,
  'file-spreadsheet': FileSpreadsheet,
} satisfies Record<string, LucideIcon>;

export type LandingIconName = keyof typeof ICONS;

export function LandingIcon({ name }: { name: LandingIconName }) {
  const Icon = ICONS[name];
  return (
    <span
      aria-hidden="true"
      className="lp-icon text-foreground inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg"
    >
      <Icon className="h-5 w-5" />
    </span>
  );
}
