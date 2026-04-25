import { redirect } from 'next/navigation';

/**
 * Root → sempre redireciona para `/login`. O middleware reescreve para `/clientes`
 * se já houver sessão (cookie de access presente).
 */
export default function HomePage() {
  redirect('/login');
}
