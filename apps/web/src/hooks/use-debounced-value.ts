/**
 * Hook simples de debounce — usado pelo input de busca da listagem de usuários
 * (Doc §8.2: "filtra em tempo real, debounce 300ms").
 */
import { useEffect, useState } from 'react';

export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const handle = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(handle);
  }, [value, delayMs]);

  return debounced;
}
