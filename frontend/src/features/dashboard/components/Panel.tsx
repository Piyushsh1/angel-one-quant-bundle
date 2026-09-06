import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

/**
 * The card shell every dashboard section sits in: a dark surface with a
 * hairline border and rounded corners. Keeping it in one place means the panel
 * chrome stays identical across the terminal.
 */
export function Panel({
  children,
  className,
  as: Tag = 'section',
  ...rest
}: {
  children: ReactNode;
  className?: string;
  as?: 'section' | 'article' | 'div';
} & React.HTMLAttributes<HTMLElement>) {
  return (
    <Tag
      className={cn(
        'rounded-xl border border-surface-700/70 bg-surface-850 p-4',
        className,
      )}
      {...rest}
    >
      {children}
    </Tag>
  );
}

/**
 * The header row inside a Panel: an icon tile, a title, optional badge, and an
 * optional trailing action (usually a "view all" link).
 */
export function PanelHeader({
  icon,
  iconClassName,
  title,
  badge,
  action,
}: {
  icon: ReactNode;
  iconClassName?: string;
  title: string;
  badge?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="mb-3 flex items-center justify-between border-b border-surface-700/60 pb-3">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            'flex h-6 w-6 items-center justify-center rounded border',
            iconClassName,
          )}
        >
          {icon}
        </span>
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
          {title}
        </h2>
        {badge}
      </div>
      {action}
    </div>
  );
}
