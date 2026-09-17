import { cn } from "@/lib/utils"

/** Pill toggle for a handful of mutually exclusive picks (period, window, form filter); the active pick is `aria-pressed`. */
export function Segmented<V extends string | number>({ value, options, onChange, className }: { value: V; options: { value: V; label: string }[]; onChange: (v: V) => void; className?: string }) {
  return (
    <div className={cn("inline-flex rounded-md border border-border p-0.5 text-xs", className)}>
      {options.map((o) => (
        <button key={String(o.value)} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)} className={cn("rounded-[5px] px-3 py-1 font-medium transition", o.value === value ? "bg-secondary" : "text-muted-foreground hover:text-foreground")}>
          {o.label}
        </button>
      ))}
    </div>
  )
}
