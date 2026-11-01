// Basiskomponenten im shadcn/ui-Stil (cva + tailwind-merge), lokal gehalten statt CLI-generiert.
import { cva, type VariantProps } from "class-variance-authority";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, SelectHTMLAttributes } from "react";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 rounded-md text-sm font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none focus-visible:outline-2 focus-visible:outline-accent",
  {
    variants: {
      variant: {
        default: "bg-accent text-white hover:bg-accent/90",
        outline: "border border-border bg-surface hover:bg-bg",
        ghost: "hover:bg-bg",
        danger: "bg-danger text-white hover:bg-danger/90",
        success: "bg-success text-white hover:bg-success/90",
      },
      size: { sm: "h-8 px-2.5", md: "h-9 px-3.5", lg: "h-10 px-5" },
    },
    defaultVariants: { variant: "default", size: "md" },
  },
);

export function Button({ className, variant, size, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants>) {
  return <button className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-lg border border-border bg-surface shadow-sm", className)} {...props} />;
}

export function CardHeader({ title, actions, subtitle }: { title: string; subtitle?: string; actions?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
      <div>
        <h2 className="text-sm font-semibold">{title}</h2>
        {subtitle && <p className="text-xs text-muted">{subtitle}</p>}
      </div>
      {actions}
    </div>
  );
}

const badgeVariants = cva("inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium whitespace-nowrap", {
  variants: {
    tone: {
      neutral: "bg-slate-100 text-slate-700",
      good: "bg-green-100 text-green-800",
      warn: "bg-orange-100 text-orange-800",
      bad: "bg-red-100 text-red-800",
      info: "bg-sky-100 text-sky-800",
      test: "bg-violet-100 text-violet-800",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export function Badge({ className, tone, ...props }: HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn("h-9 w-full rounded-md border border-border bg-surface px-2.5 text-sm outline-none focus:border-accent", className)}
      {...props}
    />
  );
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn("h-9 rounded-md border border-border bg-surface px-2 text-sm outline-none focus:border-accent", className)}
      {...props}
    />
  );
}

export function Label({ children, hint }: { children: React.ReactNode; hint?: string }) {
  return (
    <label className="mb-1 block text-xs font-medium text-muted">
      {children}
      {hint && <span className="ml-1 font-normal">({hint})</span>}
    </label>
  );
}

export function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <Card className="p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className="num mt-1 text-2xl font-semibold">{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </Card>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold">{title}</h1>
        {description && <p className="mt-0.5 text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-danger">{error}</div>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="px-4 py-10 text-center text-sm text-muted">{children}</div>;
}

export function TestDataBanner() {
  return (
    <div className="mb-4 rounded-md border border-violet-200 bg-violet-50 px-3 py-2 text-sm text-test">
      <strong>TESTDATEN:</strong> Diese Ansicht enthält erfundene Demo-Marktdaten. Nicht für Einkaufsentscheidungen verwenden.
    </div>
  );
}

export function Table({ children }: { children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">{children}</table>
    </div>
  );
}
export function Th({ children, className, ...p }: HTMLAttributes<HTMLTableCellElement>) {
  return (
    <th className={cn("border-b border-border bg-bg px-3 py-2 text-left text-xs font-medium text-muted", className)} {...p}>
      {children}
    </th>
  );
}
export function Td({ children, className, ...p }: HTMLAttributes<HTMLTableCellElement> & { colSpan?: number }) {
  return (
    <td className={cn("border-b border-border px-3 py-2 align-top", className)} {...p}>
      {children}
    </td>
  );
}
