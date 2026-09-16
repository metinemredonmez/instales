import { cn } from "@/lib/utils"
import type { Activity, Confidence, SignalType } from "@/lib/api"

const CONF: Record<Confidence, { cls: string; title: string }> = {
  EXACT: { cls: "text-exact border-exact/40 bg-exact/10", title: "Tek fon, açık tutar" },
  GROUPED: { cls: "text-grouped border-grouped/40 bg-grouped/10", title: "Birden fazla ilişkili fon; dağılım bilinmiyor" },
  INFERRED: { cls: "text-inferred border-inferred/40 bg-inferred/10", title: "İki portföy raporunun farkından türetildi" },
}

export function ConfidenceBadge({ value, className }: { value: Confidence; className?: string }) {
  const c = CONF[value]
  return (
    <span title={c.title} className={cn("inline-flex items-center rounded-sm border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider", c.cls, className)}>
      {value}
    </span>
  )
}

const ACT: Record<Activity, string> = {
  NEW: "text-positive border-positive/50 bg-positive/10",
  ADD: "text-positive border-positive/30",
  REDUCE: "text-negative border-negative/30",
  EXIT: "text-negative border-negative/50 bg-negative/10",
  HOLD: "text-muted-foreground border-border",
}

export function ActivityBadge({ value }: { value: Activity }) {
  return <span className={cn("inline-flex rounded-sm border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider", ACT[value])}>{value}</span>
}

export const SIGNAL_LABEL: Record<SignalType, { label: string; tone: "pos" | "neg" | "info" }> = {
  ACCUMULATION: { label: "Accumulation", tone: "pos" },
  DISTRIBUTION: { label: "Distribution", tone: "neg" },
  POSITIVE_DIVERGENCE: { label: "Positive divergence", tone: "pos" },
  NEGATIVE_DIVERGENCE: { label: "Negative divergence", tone: "neg" },
  NEW_POSITION_CLUSTER: { label: "New-position cluster", tone: "pos" },
  EXIT_CLUSTER: { label: "Exit cluster", tone: "neg" },
}

export function SignalBadge({ type }: { type: SignalType }) {
  const s = SIGNAL_LABEL[type]
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[11px] font-medium",
        s.tone === "pos" && "text-positive border-positive/40 bg-positive/10",
        s.tone === "neg" && "text-negative border-negative/40 bg-negative/10",
        s.tone === "info" && "text-primary border-primary/40 bg-primary/10",
      )}
    >
      {s.tone === "pos" ? "▲" : "▼"} {s.label}
    </span>
  )
}

export function ScorePill({ value, size = "md" }: { value: number | null | undefined; size?: "sm" | "md" | "lg" }) {
  if (value === null || value === undefined) return <span className="text-muted-foreground">—</span>
  const tone = value >= 70 ? "text-positive" : value <= 30 ? "text-negative" : "text-foreground"
  const track = value >= 70 ? "bg-positive" : value <= 30 ? "bg-negative" : "bg-primary"
  return (
    <span className={cn("inline-flex items-center gap-2", size === "lg" && "gap-3")}>
      <span className={cn("num font-semibold", tone, size === "sm" && "text-xs", size === "md" && "text-sm", size === "lg" && "text-3xl")}>{Math.round(value)}</span>
      <span className={cn("h-1 rounded-full bg-muted overflow-hidden", size === "sm" ? "w-8" : size === "md" ? "w-12" : "w-24")}>
        <span className={cn("block h-full rounded-full", track)} style={{ width: `${Math.max(2, Math.min(100, value))}%` }} />
      </span>
    </span>
  )
}

export function Flow({ value, market = "TR", className }: { value: number | string | null | undefined; market?: string; className?: string }) {
  const n = value === null || value === undefined ? null : Number(value)
  return (
    <span className={cn("num font-medium", n === null ? "text-muted-foreground" : n > 0 ? "text-positive" : n < 0 ? "text-negative" : "", className)}>
      {n === null ? "—" : fmt(n, market)}
    </span>
  )
}

function fmt(n: number, market: string) {
  const sign = n < 0 ? "-" : n > 0 ? "+" : ""
  const cur = market === "US" ? "$" : "₺"
  const a = Math.abs(n)
  const [num, unit] = a >= 1e9 ? [a / 1e9, "B"] : a >= 1e6 ? [a / 1e6, "M"] : a >= 1e3 ? [a / 1e3, "K"] : [a, ""]
  return `${sign}${cur}${num.toFixed(num >= 100 || unit === "" ? 0 : 1)}${unit}`
}
