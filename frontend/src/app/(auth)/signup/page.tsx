import type { Metadata } from 'next';
import { AuthCard } from '@/features/auth/components/AuthCard';
import { SignupForm } from '@/features/auth/components/SignupForm';

export const metadata: Metadata = {
  title: 'Create account',
};

export default function SignupPage() {
  return (
    <AuthCard
      active="signup"
      title="Create your account"
      subtitle="You will connect your broker in the next step."
    >
      <SignupForm />
    </AuthCard>
  );
}
