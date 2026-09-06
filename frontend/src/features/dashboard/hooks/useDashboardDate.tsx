'use client';

import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

/**
 * The selected trading date for the whole terminal.
 *
 * Defaults to today. Every date-aware panel (metrics, P&L, positions, orders,
 * reports) reads this so a single date-picker drives the entire dashboard. The
 * value is `undefined` for today (so queries hit the live endpoints without a
 * date param) and a `YYYY-MM-DD` string when the user picks a past day.
 */

/** Today's date in IST as YYYY-MM-DD — the market's trading date. */
export function todayIst(): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    timeZone: 'Asia/Kolkata',
  }).format(new Date());
  return parts; // en-CA yields YYYY-MM-DD
}

interface DashboardDateValue {
  /** The selected date, always YYYY-MM-DD. */
  date: string;
  /** True when the selection is today (drives "live" vs "history" UI). */
  isToday: boolean;
  /** The value to pass to the API: undefined for today, else the date. */
  queryDate: string | undefined;
  setDate: (date: string) => void;
  resetToToday: () => void;
}

const DashboardDateContext = createContext<DashboardDateValue | null>(null);

export function DashboardDateProvider({ children }: { children: ReactNode }) {
  const [date, setDate] = useState<string>(() => todayIst());

  const value = useMemo<DashboardDateValue>(() => {
    const today = todayIst();
    const isToday = date === today;
    return {
      date,
      isToday,
      queryDate: isToday ? undefined : date,
      setDate,
      resetToToday: () => setDate(today),
    };
  }, [date]);

  return (
    <DashboardDateContext.Provider value={value}>
      {children}
    </DashboardDateContext.Provider>
  );
}

export function useDashboardDate(): DashboardDateValue {
  const ctx = useContext(DashboardDateContext);
  if (!ctx) {
    // Safe fallback so a panel used outside the provider still works (today).
    const today = todayIst();
    return {
      date: today,
      isToday: true,
      queryDate: undefined,
      setDate: () => {},
      resetToToday: () => {},
    };
  }
  return ctx;
}
