import { useEffect, useState, type ReactNode } from "react"
import { Link } from "react-router-dom"
import { Lock, X } from "lucide-react"
import { isPlanLimit, type PlanCode, type PlanLimitError } from "@/lib/api"
import { hasFeature, useAuth } from "@/lib/auth"
import { useI18n, type T } from "@/lib/i18n"
import { tr, type Key } from "@/i18n/tr"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const PLAN_NAME: Record<PlanCode, Key> = { FREE: "plan.free", PRO: "plan.pro", PRO_PLUS: "plan.proPlus" }
/** Rank used to tell an upgrade from a downgrade on the plan page. */
export const PLAN_ORDER: Record<PlanCode, number> = { FREE: 0, PRO: 1, PRO_PLUS: 2 }
export const isPlanCode = (p: unknown): p is PlanCode => p === "FREE" || p === "PRO" || p === "PRO_PLUS"
export const planName = (t: T, p: PlanCode | string) => (isPlanCode(p) ? t(PLAN_NAME[p]) : p)
/** Feature key → its label; a key the dictionary has no word for reads as the API sends it. */
export const featureLabel = (t: T, feature: string) => {
  const k = `plan.f.${feature}`
  return k in tr ? t(k as Key) : feature
}
/** "Portföy takibi planında yok — Pro ile açılır." / "Uyarı kuralı limitine ulaşıldı (3) — Pro ile artar." */
export const limitMessage = (t: T, e: PlanLimitError) =>
  e.limit === null || e.limit === 0
    ? t("plan.limit.none", { feature: featureLabel(t, e.feature), p: planName(t, e.upgrade) })
    : t("plan.limit.reached", { feature: featureLabel(t, e.feature), n: e.limit, p: planName(t, e.upgrade) })

/**
 * The calm "not in your plan" card: what is locked, which plan opens it, one button to /plan. Rendered by PlanGate and
 * by pages that catch a 402 of their own; never a wall — the page's header stays above it.
 */
export function PlanLockedCard({ feature, upgrade = "PRO", limit = null, title, body, className }: { feature?: string; upgrade?: PlanCode; limit?: number | null; title?: string; body?: string; className?: string }) {
  const { t } = useI18n()
  const p = planName(t, upgrade)
  const line = body ?? (feature ? (limit === null || limit === 0 ? t("plan.limit.none", { feature: featureLabel(t, feature), p }) : t("plan.limit.reached", { feature: featureLabel(t, feature), n: limit, p })) : undefined)
  return (
    <div role="status" className={cn("rise mx-auto max-w-md rounded-lg border border-border bg-card px-6 py-8 text-center", className)}>
      <div className="mx-auto grid size-10 place-items-center rounded-full bg-muted text-muted-foreground"><Lock className="size-5" /></div>
      <div className="mt-3 text-sm font-semibold">{title ?? t("plan.locked.title")}</div>
      {line && <p className="mt-1 text-sm text-muted-foreground">{line}</p>}
      <div className="mt-1 text-xs font-medium text-primary">{t("plan.upgradeTo", { p })}</div>
      <Button asChild size="sm" className="mt-4"><Link to="/plan">{t("plan.seePlans")}</Link></Button>
    </div>
  )
}

/**
 * Wraps a plan-gated page body. Locked when the account's matrix (/auth/me `features`) says `feature` is off or at
 * zero, or when the page's own request came back 402 (`error` — the API is the authority; the matrix only saves the
 * round trip). Admins pass; a matrix that does not name the feature passes too, so an API without plans changes nothing.
 * The matrix is the session's copy from sign-in: a client-side lock re-reads /auth/me once, so an upgrade, an admin
 * grant or the switch turned off reaches the page without a hard reload.
 */
export function PlanGate({ feature, upgrade = "PRO", error, title, body, children }: { feature: string | string[]; upgrade?: PlanCode; error?: unknown; title?: string; body?: string; children: ReactNode }) {
  const { user, refreshUser } = useAuth()
  const locked = !hasFeature(user, feature)
  useEffect(() => { if (locked && user) void refreshUser?.().catch(() => {}) }, [locked]) // eslint-disable-line react-hooks/exhaustive-deps
  if (isPlanLimit(error)) return <PlanLockedCard feature={error.feature} upgrade={error.upgrade} limit={error.limit} title={title} body={body} />
  if (locked) return <PlanLockedCard feature={Array.isArray(feature) ? feature[0] : feature} upgrade={upgrade} title={title} body={body} />
  return <>{children}</>
}

/**
 * A control the plan does not include, in place: the lock, one line naming the plan that opens it, a link to /plan.
 * For the small in-line controls (the speak button, the push switch) where the full card would be out of scale.
 */
export function PlanLockedInline({ text, upgrade = "PRO", className }: { text: string; upgrade?: PlanCode; className?: string }) {
  const { t } = useI18n()
  return (
    <Link to="/plan" title={t("plan.upgradeTo", { p: planName(t, upgrade) })} className={cn("inline-flex items-center gap-1 rounded-md border border-dashed border-border px-2 py-1 text-xs text-muted-foreground hover:text-foreground", className)}>
      <Lock className="size-3.5" /> {text}
    </Link>
  )
}

/**
 * Global 402 handling: lib/api announces every plan_limit answer on `instilens:plan-limit`; this shows it as a toast
 * with the way to /plan, so a mutation the page did not wrap (a sixth watchlist, the 51st rule) still explains itself
 * instead of failing quietly. One toast at a time, the newest wins, gone after eight seconds or a click on ✕.
 */
export function PlanLimitToast() {
  const { t } = useI18n()
  const [err, setErr] = useState<PlanLimitError | null>(null)
  useEffect(() => {
    const on = (e: Event) => { const d = (e as CustomEvent<unknown>).detail; if (isPlanLimit(d)) setErr(d) }
    window.addEventListener("instilens:plan-limit", on)
    return () => window.removeEventListener("instilens:plan-limit", on)
  }, [])
  useEffect(() => {
    if (!err) return
    const id = setTimeout(() => setErr(null), 8000)
    return () => clearTimeout(id)
  }, [err])
  if (!err) return null
  return (
    <div role="status" aria-live="polite" className="rise fixed bottom-4 right-4 z-[60] w-[min(360px,calc(100vw-2rem))] rounded-lg border border-border bg-popover p-3 text-sm shadow-xl">
      <div className="flex items-start gap-2.5">
        <Lock className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="font-medium">{t("plan.toast.title")}</div>
          <div className="mt-0.5 text-xs text-muted-foreground">{limitMessage(t, err)}</div>
          <Link to="/plan" onClick={() => setErr(null)} className="mt-1.5 inline-block text-xs font-medium text-primary hover:underline">{t("plan.seePlans")} →</Link>
        </div>
        <button type="button" onClick={() => setErr(null)} aria-label={t("common.close")} className="rounded-sm p-0.5 text-muted-foreground hover:text-foreground"><X className="size-4" /></button>
      </div>
    </div>
  )
}
