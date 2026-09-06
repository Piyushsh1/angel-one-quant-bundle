import type { Metadata } from 'next';
import { AuthCard } from '@/features/auth/components/AuthCard';
import { LoginForm } from '@/features/auth/components/LoginForm';

export const metadata: Metadata = {
  title: 'Sign in',
};

export default function LoginPage() {
  return (
    <AuthCard
      active="login"
      title="Welcome back"
      subtitle="Sign in to manage your strategies and broker connections."
    >
      <LoginForm />
    </AuthCard>
  );
}
