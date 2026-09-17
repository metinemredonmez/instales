import { cn } from "@/lib/utils"
import type { Activity, Confidence, SignalType } from "@/lib/api"
import { useI18n, type T } from "@/lib/i18n"
import { useCountUp, useFlash } from "@/lib/motion"

const CONF: Record<Confidence, { cls: string; key: "conf.exact" | "conf.grouped" | "conf.inferred" }> = {
  EXACT: { cls: "text-exact border-exact/40 bg-exact/10", key: "conf.exact" },
  GROUPED: { cls: "text-grouped border-grouped/40 bg-grouped/10", key: "conf.grouped" },
  INFERRED: { cls: "text-inferred border-inferred/40 bg-inferred/10", key: "conf.inferred" },
}

export function ConfidenceBadge({ value, className }: { value: Confidence; className?: string }) {
  const { t } = useI18n()
  const c = CONF[value]
  return (
    <span title={t(c.key)} className={cn("inline-flex items-center rounded-sm border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider", c.cls, className)}>
      {value}
    </span>
  )
}

/** Activity tone classes; exported so compact chips (Moves parties) share the badge colours. */
export const ACT: Record<Activity, string> = {
  NEW: "text-positive border-positive/50 bg-positive/10",
  ADD: "text-positive border-positive/30",
  REDUCE: "text-negative border-negative/30",
  EXIT: "text-negative border-negative/50 bg-negative/10",
  HOLD: "text-muted-foreground border-border",
}

export function ActivityBadge({ value }: { value: Activity }) {
  return <span className={cn("inline-flex rounded-sm border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider", ACT[value])}>{value}</span>
}

const SIGNAL_TONE: Record<SignalType, { key: `signal.${string}` & Parameters<T>[0]; tone: "pos" | "neg" | "info" }> = {
  ACCUMULATION: { key: "signal.accumulation", tone: "pos" },
  DISTRIBUTION: { key: "signal.distribution", tone: "neg" },
  POSITIVE_DIVERGENCE: { key: "signal.positiveDivergence", tone: "pos" },
  NEGATIVE_DIVERGENCE: { key: "signal.negativeDivergence", tone: "neg" },
  NEW_POSITION_CLUSTER: { key: "signal.newPositionCluster", tone: "pos" },
  EXIT_CLUSTER: { key: "signal.exitCluster", tone: "neg" },
}
export const SIGNAL_TYPES = Object.keys(SIGNAL_TONE) as SignalType[]
export const signalLabel = (t: T, type: SignalType) => t(SIGNAL_TONE[type].key)

export function SignalBadge({ type }: { type: SignalType }) {
  const { t } = useI18n()
  const s = { ...SIGNAL_TONE[type], label: signalLabel(t, type) }
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
  const shown = useCountUp(value)
  const flash = useFlash(value === null || value === undefined ? null : Math.round(value))
  if (shown === null || value === null || value === undefined) return <span className="text-muted-foreground">—</span>
  const tone = value >= 70 ? "text-positive" : value <= 30 ? "text-negative" : "text-foreground"
  const track = value >= 70 ? "bg-positive" : value <= 30 ? "bg-negative" : "bg-primary"
  return (
    <span className={cn("inline-flex items-center gap-2 rounded-sm", size === "lg" && "gap-3", flash)}>
      <span className={cn("num num-anim font-semibold", tone, size === "sm" && "text-xs", size === "md" && "text-sm", size === "lg" && "text-3xl")}>{Math.round(shown)}</span>
      <span className={cn("h-1 rounded-full bg-muted overflow-hidden", size === "sm" ? "w-8" : size === "md" ? "w-12" : "w-24")}>
        <span className={cn("bar-anim block h-full rounded-full", track)} style={{ width: `${Math.max(2, Math.min(100, shown))}%` }} />
      </span>
    </span>
  )
}

export function Flow({ value, market = "TR", className }: { value: number | string | null | undefined; market?: string; className?: string }) {
  const n = value === null || value === undefined ? null : Number(value)
  const shown = useCountUp(n)
  const flash = useFlash(n, n !== null && n < 0 ? "neg" : "pos")
  return (
    <span className={cn("num num-anim rounded-sm font-medium", n === null ? "text-muted-foreground" : n > 0 ? "text-positive" : n < 0 ? "text-negative" : "", flash, className)}>
      {shown === null ? "—" : fmt(shown, market)}
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

/** Plain integer that counts up/down and flashes when it changes (stat tiles). */
export function Num({ value, className }: { value: number; className?: string }) {
  const shown = useCountUp(value)
  const flash = useFlash(value)
  return <span className={cn("num num-anim inline-block rounded-sm", flash, className)}>{Math.round(shown ?? value)}</span>
}
