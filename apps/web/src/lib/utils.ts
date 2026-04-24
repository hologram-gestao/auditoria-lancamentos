import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Helper padrão do shadcn/ui para mesclar classes Tailwind condicionais. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
