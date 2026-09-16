import { cn } from "@/lib/utils"

export function Section({ title, hint, right, children, className }: { title: string; hint?: string; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("rise rounded-lg border border-border bg-card", className)}>
      <header className="flex items-center justify-between gap-3 border-b border-border/70 px-4 py-2.5">
        <div className="flex items-baseline gap-2">
          <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
          {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
        </div>
        {right}
      </header>
      <div>{children}</div>
    </section>
  )
}

export function Stat({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: "pos" | "neg" }) {
  return (
    <div className="rise rounded-lg border border-border bg-card px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={cn("num mt-1 text-2xl font-semibold", tone === "pos" && "text-positive", tone === "neg" && "text-negative")}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>}
    </div>
  )
}
