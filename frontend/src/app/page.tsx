import { redirect } from 'next/navigation';

/**
 * Root route.
 *
 * There is no public landing page yet, so send visitors to sign-in. Once a
 * session check exists this becomes: authenticated → /dashboard,
 * otherwise → /login.
 */
export default function RootPage() {
  redirect('/login');
}
