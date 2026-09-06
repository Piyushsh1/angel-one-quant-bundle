'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { Alert } from '@/components/ui/Alert';
import { api } from '@/lib/api-client';
import { authKeys } from '@/features/auth/hooks/authKeys';
import { ROUTES } from '@/features/auth/lib/routing';
import { userFacingMessage } from '@/lib/errors';

/**
 * Link the user's Telegram so they receive their own trade alerts.
 *
 * How it works (and why it's built this way):
 *   A Telegram bot cannot message someone by phone number — the user must
 *   contact the bot first. So this is a two-step handshake:
 *     1. We POST the phone number and get back a one-time bot deep link.
 *     2. The user opens it and taps Start; the backend captures their chat and
 *        links it to this account, then sends a confirmation message.
 *   Meanwhile we poll link status and advance automatically once it flips.
 *
 * One shared bot serves every user — isolation is by chat id, so each user only
 * ever sees their own trades.
 */

const linkResponseSchema = z.object({
  linked: z.boolean(),
  deepLink: z.string().url(),
  botUsername: z.string(),
});

const statusSchema = z.object({ linked: z.boolean() });

type Phase = 'enter' | 'awaiting';

export function LinkTelegramFlow() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [phone, setPhone] = useState('');
  const [touched, setTouched] = useState(false);
  const [phase, setPhase] = useState<Phase>('enter');
  const [deepLink, setDeepLink] = useState('');
  const [botUsername, setBotUsername] = useState('');
  const advanced = useRef(false);

  const link = useMutation({
    mutationFn: (phoneNumber: string) =>
      api.post('/notifications/telegram/link', {
        body: { phone: phoneNumber },
        schema: linkResponseSchema,
      }),
    onSuccess: async (data) => {
      setDeepLink(data.deepLink);
      setBotUsername(data.botUsername);
      // Already linked from a prior attempt — just move on.
      if (data.linked) {
        await advance();
        return;
      }
      setPhase('awaiting');
      // Open the bot for the user. Popup blockers may stop this; the on-screen
      // button is the reliable fallback.
      window.open(data.deepLink, '_blank', 'noopener,noreferrer');
    },
    retry: false,
  });

  // Poll link status only while we're waiting for the user to tap Start.
  const status = useQuery({
    queryKey: [...authKeys.all, 'telegramStatus'],
    queryFn: ({ signal }) =>
      api.get('/notifications/telegram/status', { schema: statusSchema, signal }),
    enabled: phase === 'awaiting',
    refetchInterval: phase === 'awaiting' ? 2000 : false,
  });

  const advance = async () => {
    if (advanced.current) return;
    advanced.current = true;
    await queryClient.invalidateQueries({ queryKey: authKeys.currentUser() });
    router.replace(ROUTES.dashboard);
  };

  useEffect(() => {
    if (status.data?.linked) {
      void advance();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status.data?.linked]);

  // Permissive check; the backend is the real validator.
  const valid = /^\+?[0-9\s-]{8,15}$/.test(phone.trim());

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!valid) return;
    link.mutate(phone.trim());
  };

  const skip = () => router.replace(ROUTES.dashboard);

  if (phase === 'awaiting') {
    return (
      <div className="space-y-5">
        <Alert tone="info" title="Almost there — tap Start in Telegram">
          We opened <span className="font-semibold">@{botUsername}</span> in
          Telegram. Tap <span className="font-semibold">Start</span> there and
          this page continues on its own. You&apos;ll get a confirmation message
          the moment it&apos;s linked.
        </Alert>

        <div className="flex items-center justify-center gap-2 text-sm text-slate-400">
          <span className="h-2 w-2 animate-pulse rounded-full bg-brand-400" />
          Waiting for you to tap Start…
        </div>

        <div className="flex flex-col gap-3">
          <a href={deepLink} target="_blank" rel="noopener noreferrer">
            <Button type="button" fullWidth size="lg">
              Open Telegram again
            </Button>
          </a>
          <Button type="button" variant="ghost" onClick={skip}>
            Skip for now
          </Button>
        </div>

        <p className="text-center text-[11px] text-slate-500">
          Didn&apos;t work? The link expires after a while — go back and request
          a fresh one.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="space-y-5">
      {link.error && (
        <Alert tone="error" title="Could not start Telegram linking">
          {userFacingMessage(link.error)}
        </Alert>
      )}

      <Input
        label="Mobile number"
        type="tel"
        autoComplete="tel"
        placeholder="+91 98765 43210"
        value={phone}
        onChange={(e) => setPhone(e.target.value)}
        onBlur={() => setTouched(true)}
        error={touched && !valid ? 'Enter a valid mobile number' : undefined}
        hint="Next you'll tap Start in our Telegram bot — that's what lets us message you."
        disabled={link.isPending}
        required
      />

      <div className="flex items-center gap-3">
        <Button type="button" variant="ghost" onClick={skip} disabled={link.isPending}>
          Skip for now
        </Button>
        <Button
          type="submit"
          fullWidth
          size="lg"
          isLoading={link.isPending}
          loadingText="Opening Telegram…"
        >
          Link Telegram
        </Button>
      </div>

      <p className="text-center text-[11px] text-slate-500">
        You can enable notifications later in Settings, but we recommend linking
        before switching any engine to Auto mode.
      </p>
    </form>
  );
}
