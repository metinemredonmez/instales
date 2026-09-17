import { useEffect, useMemo, useState, type FormEvent } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "react-router-dom"
import { Check, CreditCard, LogOut, Minus, Trash2, Users } from "lucide-react"
import { ApiError, LIVE_STATUSES, api, isNotConfigured, type BillingPlans, type PlanCode, type PlanInfo, type PlanSource, type Subscription } from "@/lib/api"
import { useAuth, type User } from "@/lib/auth"
import { useI18n, type T } from "@/lib/i18n"
import { fmtDate, fmtPrice, fmtQty } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { PLAN_ORDER, isPlanCode, featureLabel, planName } from "@/components/domain/PlanGate"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

const input = "h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring/40"

/**
 * Columns in plan order whatever order the API lists them; a plan the API leaves out is not invented. Rows are the
 * union of the plans' feature keys, first-seen order (FREE's first), so the matrix is the API's, not a copy kept here.
 */
export function matrixRows(plans: BillingPlans): { columns: PlanInfo[]; rows: string[] } {
  const columns = [...plans.plans].filter((p) => isPlanCode(p.code)).sort((a, b) => PLAN_ORDER[a.code] - PLAN_ORDER[b.code])
  const rows: string[] = []
  for (const p of columns) for (const k of Object.keys(p.features ?? {})) if (!rows.includes(k)) rows.push(k)
  return { columns, rows }
}
/** "ücretsiz" / "$9,00/ay" / "—" — the column head's price line: the Price object's own currency, the page's when it has none. */
export function priceLabel(t: T, p: PlanInfo, currency: string): string {
  if (p.code === "FREE") return t("plan.price.free")
  if (!p.price || p.price.amount === null || p.price.amount === undefined) return "—"
  if (p.price.amount === 0) return t("plan.price.free")
  return `${fmtPrice(p.price.amount, (p.price.currency || currency).toUpperCase())}${t("plan.price.month")}`
}
/** "KDV dahil" / "KDV hariç" from the Price's own tax_behavior; nothing when Stripe states nothing (unspecified). */
export function taxLabel(t: T, p: PlanInfo): string | null {
  const b = p.price?.tax_behavior
  return b === "inclusive" ? t("plan.tax.inclusive") : b === "exclusive" ? t("plan.tax.exclusive") : null
}
/** A paid subscription the provider is billing right now: a plan change goes through the portal, never a second checkout. */
export const isLiveStripe = (sub: Subscription | null | undefined) => !!sub && sub.provider === "stripe" && LIVE_STATUSES.includes(sub.status)
/** How long the page keeps re-reading /billing/me after a successful checkout while the webhook has not landed yet. */
export const CHECKOUT_POLL_MS = 3000
export const CHECKOUT_POLL_MAX = 20
/**
 * Organisations are a PRO_PLUS feature: the matrix's `org_seats` decides when the payload has it, else the plan itself.
 * Admins see the section either way.
 */
export function orgAllowed(user: User | null, plan: PlanCode): boolean {
  if (!user) return false
  if (user.role === "ADMIN") return true
  const seats = user.features?.org_seats
  if (typeof seats === "number") return seats > 0
  if (typeof seats === "boolean") return seats
  return plan === "PRO_PLUS"
}

/**
 * /plan — the matrix with the prices, the account's current plan and where it comes from, the upgrade buttons
 * (Stripe Checkout, mode=subscription) and the Billing Portal, and the organisation section for Pro+.
 * Payments not configured on the deployment (`configured: false`, or a 503 from checkout/portal) is a calm state,
 * never a stand-in checkout: the matrix stays readable, the buttons say why they are off. A live Stripe subscriber
 * never checks out again: a higher tier is a plan change in the portal (the API answers 409 to a checkout anyway).
 * Back from Stripe (`?checkout=success|cancel`) the page says so, keeps re-reading /billing/me until the webhook has
 * landed the plan, and refreshes the session so the gated pages open without a reload.
 */
export function PlanPage() {
  const { t } = useI18n()
  const { user, refreshUser } = useAuth()
  const [params, setParams] = useSearchParams()
  // Read once, then taken off the address bar so a reload does not announce the payment twice.
  const [returned] = useState<"success" | "cancel" | null>(() => { const c = params.get("checkout"); return c === "success" || c === "cancel" ? c : null })
  useEffect(() => { if (params.has("checkout") || params.has("session_id")) setParams({}, { replace: true }) }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const [polls, setPolls] = useState(0)
  const plans = useQuery({ queryKey: ["billing", "plans"], queryFn: api.billingPlans })
  // An API without billing (404) still has a plan on the user; the card then reads the session. Back from a paid
  // checkout it is re-read every few seconds until the webhook has landed the subscription (or a minute has passed).
  const me = useQuery({ queryKey: ["billing", "me"], queryFn: api.billingMe, retry: false, refetchInterval: (q) => (returned === "success" && polls < CHECKOUT_POLL_MAX && !isLiveStripe(q.state.data?.subscription) ? CHECKOUT_POLL_MS : false) })
  const [notConfigured, setNotConfigured] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const go = (r: { url: string }) => { setMsg(t("plan.redirecting")); window.location.assign(r.url) }
  const fail = (e: unknown) => {
    if (isNotConfigured(e)) setNotConfigured(true)
    else if (e instanceof ApiError && e.status === 409) setMsg(t("plan.subscriptionExists"))
    else setMsg((e as Error).message)
  }
  const checkout = useMutation({ mutationFn: api.billingCheckout, onSuccess: go, onError: fail })
  const portal = useMutation({ mutationFn: (flow?: "subscription_update") => api.billingPortal(flow), onSuccess: go, onError: fail })

  const plan: PlanCode = me.data?.plan ?? user?.plan ?? "FREE"
  const source: PlanSource | undefined = me.data?.source ?? user?.plan_source
  const configured = (plans.data?.configured ?? false) && !notConfigured
  const sub = me.data?.subscription ?? null
  // The portal manages a Stripe subscription; a manual grant or an org seat has nothing to manage there.
  const canPortal = configured && sub?.provider === "stripe"
  const liveStripe = configured && isLiveStripe(sub)
  const matrix = useMemo(() => (plans.data ? matrixRows(plans.data) : null), [plans.data])
  // The session carries the plan from sign-in; when /billing/me knows better, bring the session up to date.
  useEffect(() => {
    if (!me.data || !user) return
    if (me.data.plan !== user.plan) void refreshUser?.().catch(() => {})
    if (returned === "success") setPolls((n) => n + 1)
  }, [me.data, me.dataUpdatedAt]) // eslint-disable-line react-hooks/exhaustive-deps
  // While the payment is being applied nothing else is started: no second checkout on a page that is about to change.
  const busy = returned === "success" && polls < CHECKOUT_POLL_MAX && !isLiveStripe(sub)

  return (
    <div className="space-y-5">
      <div><h1 className="text-2xl font-semibold tracking-tight">{t("plan.title")}</h1><p className="text-sm text-muted-foreground">{t("plan.sub")}</p></div>

      <CurrentPlanCard plan={plan} source={source} sub={sub} isAdmin={user?.role === "ADMIN"} canPortal={canPortal} onPortal={() => { setMsg(null); portal.mutate(undefined) }} pending={portal.isPending} />

      {returned && (
        <div role="status" className="rise rounded-md border border-border bg-card px-3 py-2 text-sm">{t(returned === "success" ? "plan.checkout.success" : "plan.checkout.cancel")}</div>
      )}
      {plans.data && !configured && (
        <div role="status" className="rise flex items-start gap-3 rounded-lg border border-border bg-card px-4 py-3 text-sm">
          <CreditCard className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div><div className="font-medium">{t("plan.notConfigured")}</div><div className="text-xs text-muted-foreground">{t("plan.notConfigured.hint")}</div></div>
        </div>
      )}
      {msg && <div role="status" className="rounded-md border border-border bg-card px-3 py-2 text-sm">{msg}</div>}

      <Section title={t("plan.matrix")} hint={t("plan.matrix.hint")}>
        {plans.isLoading ? (
          <Skeleton className="m-4 h-64" />
        ) : plans.isError || !matrix ? (
          <div className="px-4 py-6 text-sm text-muted-foreground">{t("plan.error")}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/60 align-bottom">
                  <th className="px-4 py-3 text-left text-[11px] font-medium uppercase tracking-wider text-muted-foreground" />
                  {matrix.columns.map((p) => (
                    <th key={p.code} scope="col" className={cn("px-3 py-3 text-center", p.code === plan && "bg-primary/5")}>
                      <div className="text-sm font-semibold">{planName(t, p.code)}</div>
                      <div className="num text-xs text-muted-foreground">{priceLabel(t, p, plans.data!.currency)}</div>
                      {taxLabel(t, p) && <div className="text-[10px] font-normal text-muted-foreground">{taxLabel(t, p)}</div>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {matrix.rows.map((k) => (
                  <tr key={k} className="border-b border-border/40 last:border-0">
                    <th scope="row" className="px-4 py-2 text-left font-normal text-muted-foreground">{featureLabel(t, k)}</th>
                    {matrix.columns.map((p) => <td key={p.code} className={cn("num px-3 py-2 text-center", p.code === plan && "bg-primary/5")}><FeatureCell value={p.features?.[k]} /></td>)}
                  </tr>
                ))}
                <tr>
                  <td className="px-4 py-3" />
                  {matrix.columns.map((p) => (
                    <td key={p.code} className={cn("px-3 py-3 text-center", p.code === plan && "bg-primary/5")}>
                      {p.code === plan ? (
                        <span className="rounded-sm border border-primary/40 bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">{t("plan.yours")}</span>
                      ) : PLAN_ORDER[p.code] > PLAN_ORDER[plan] ? (
                        liveStripe ? (
                          // The existing subscription changes plan in the portal (prorated by Stripe); a checkout would open a second one.
                          <Button type="button" size="sm" variant="outline" disabled={portal.isPending || busy} title={t("plan.changePlan.hint")} onClick={() => { setMsg(null); portal.mutate("subscription_update") }}>{t("plan.changePlan")}</Button>
                        ) : (
                          <Button type="button" size="sm" disabled={!configured || checkout.isPending || busy} title={configured ? undefined : t("plan.notConfigured")} onClick={() => { setMsg(null); checkout.mutate(p.code) }}>{t("plan.upgrade")}</Button>
                        )
                      ) : null}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <OrgSection allowed={orgAllowed(user, plan)} user={user} />
    </div>
  )
}

/** ✓ (with its word for screen readers), — for off, the cap as a number. */
function FeatureCell({ value }: { value: boolean | number | undefined }) {
  const { t } = useI18n()
  if (value === undefined) return <span className="text-muted-foreground">—</span>
  if (value === true) return <span className="inline-flex items-center text-positive" title={t("plan.included")}><Check className="size-4" /><span className="sr-only">{t("plan.included")}</span></span>
  if (value === false || value === 0) return <span className="inline-flex items-center text-muted-foreground" title={t("plan.notIncluded")}><Minus className="size-4" /><span className="sr-only">{t("plan.notIncluded")}</span></span>
  return <span>{value < 0 ? t("plan.unlimited") : fmtQty(value)}</span>
}

function CurrentPlanCard({ plan, source, sub, isAdmin, canPortal, onPortal, pending }: { plan: PlanCode; source?: PlanSource; sub: Subscription | null; isAdmin: boolean; canPortal: boolean; onPortal: () => void; pending: boolean }) {
  const { t } = useI18n()
  const details: string[] = []
  if (source) details.push(t(`plan.source.${source}`))
  if (sub) {
    details.push(t(`plan.status.${sub.status}`))
    if (sub.current_period_end) details.push(sub.provider === "manual" ? t("plan.manualUntil", { d: fmtDate(sub.current_period_end) }) : t("plan.renews", { d: fmtDate(sub.current_period_end) }))
    if (sub.cancel_at_period_end) details.push(t("plan.cancelAtEnd"))
  }
  return (
    <div className="rise flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{t("plan.current")}</div>
      <span className="rounded-sm border border-primary/40 bg-primary/10 px-2 py-0.5 text-sm font-semibold text-primary">{planName(t, plan)}</span>
      {isAdmin && <span className="rounded-sm border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">{t("plan.admin")}</span>}
      {details.length > 0 && <span className="text-xs text-muted-foreground">{details.join(" · ")}</span>}
      {canPortal && <Button type="button" variant="outline" size="sm" className="ml-auto" onClick={onPortal} disabled={pending}><CreditCard /> {t("plan.manageBilling")}</Button>}
    </div>
  )
}

/**
 * Pro+ organisations: create one, invite by e-mail (a signed link goes out on the existing mail path), remove members,
 * leave (a member's own row) or close it (the owner). A member inherits the org plan when it is higher than their own.
 * Owner-only controls are hidden for members.
 */
function OrgSection({ allowed, user }: { allowed: boolean; user: User | null }) {
  const { t } = useI18n()
  const qc = useQueryClient()
  // 404 = the deployment has no org routes yet, or the user is in none; both read as "no organisation".
  const org = useQuery({ queryKey: ["org"], queryFn: () => api.org().catch((e: unknown) => { if (e instanceof ApiError && e.status === 404) return null; throw e }), enabled: allowed, retry: false })
  const [name, setName] = useState("")
  const [email, setEmail] = useState("")
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ["org"] })
  const create = useMutation({ mutationFn: () => api.createOrg(name.trim()), onSuccess: () => { setName(""); setMsg(null); refresh() }, onError: (e) => setMsg({ ok: false, text: (e as Error).message }) })
  const invite = useMutation({ mutationFn: () => api.orgInvite(email.trim()), onSuccess: (r) => { setMsg({ ok: r.sent, text: t(r.sent ? "org.invited" : "org.invitedNoMail", { email: email.trim() }) }); setEmail(""); refresh() }, onError: (e) => setMsg({ ok: false, text: (e as Error).message }) })
  const remove = useMutation({ mutationFn: api.orgRemoveMember, onSuccess: refresh, onError: (e) => setMsg({ ok: false, text: (e as Error).message }) })
  const close = useMutation({ mutationFn: api.deleteOrg, onSuccess: refresh, onError: (e) => setMsg({ ok: false, text: (e as Error).message }) })
  if (!allowed) return <Section title={t("org.title")} hint={t("org.hint")}><div className="px-4 py-6 text-sm text-muted-foreground">{t("org.locked")}</div></Section>
  const o = org.data ?? null
  const owner = !!o && (o.is_owner ?? (!!user && o.owner_user_id === user.id))
  // A pending invitation holds its seat until it is accepted or removed, so the count is every row, owner included.
  const used = o ? o.seats_used ?? o.members.length : 0
  return (
    <Section title={t("org.title")} hint={o ? t("org.seats", { used, n: o.seats }) : t("org.hint")}>
      {org.isLoading ? (
        <Skeleton className="m-4 h-24" />
      ) : org.isError ? (
        <div className="px-4 py-6 text-sm text-muted-foreground">{t("org.error")}</div>
      ) : !o ? (
        <form onSubmit={(e: FormEvent) => { e.preventDefault(); if (name.trim()) create.mutate() }} className="space-y-3 p-4 text-sm">
          <div className="text-muted-foreground">{t("org.none")} {t("org.inherit")}</div>
          <div className="flex flex-wrap gap-2">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("org.name")} minLength={2} maxLength={64} className={cn(input, "sm:w-72")} required />
            <Button type="submit" size="sm" disabled={create.isPending}><Users /> {t("org.create")}</Button>
          </div>
          {msg && <div className={cn("rounded-md border px-3 py-2 text-xs", msg.ok ? "border-positive/40 bg-positive/10" : "border-negative/40 bg-negative/10")}>{msg.text}</div>}
        </form>
      ) : (
        <div className="text-sm">
          <div className="flex flex-wrap items-center gap-2 border-b border-border/60 px-4 py-3">
            <Users className="size-4 text-muted-foreground" />
            <span className="font-medium">{o.name}</span>
            <span className="rounded-sm border border-primary/40 bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-primary">{planName(t, o.plan)}</span>
            <span className="text-xs text-muted-foreground">{t("org.inherit")}</span>
          </div>
          <ul className="divide-y divide-border/60" aria-label={t("org.members")}>
            {o.members.map((m) => (
              <li key={m.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
                <span className="font-medium">{m.email}</span>
                {m.name && <span className="truncate text-muted-foreground">{m.name}</span>}
                <span className="rounded-sm border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">{t(`org.role.${m.role}`)}</span>
                {(m.pending ?? !m.accepted_at) && m.role !== "OWNER" && <span className="text-[11px] text-warning">{t("org.pending")}</span>}
                {owner && m.role !== "OWNER" && <button type="button" onClick={() => remove.mutate(m.id)} className="ml-auto text-muted-foreground hover:text-negative" aria-label={`${t("org.remove")}: ${m.email}`}><Trash2 className="size-4" /></button>}
                {/* A member's own row: leaving is the same DELETE, on their own seat. */}
                {!owner && m.role !== "OWNER" && !!user && m.user_id === user.id && (
                  <button type="button" onClick={() => { if (confirm(t("org.leaveConfirm", { name: o.name }))) remove.mutate(m.id) }} disabled={remove.isPending} className="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-negative"><LogOut className="size-3.5" /> {t("org.leave")}</button>
                )}
              </li>
            ))}
          </ul>
          {!owner && msg && <div className="border-t border-border/60 px-4 py-2 text-xs">{msg.text}</div>}
          {owner && (
            <form onSubmit={(e: FormEvent) => { e.preventDefault(); if (email.trim()) invite.mutate() }} className="space-y-2 border-t border-border/60 p-4">
              <div className="flex flex-wrap gap-2">
                <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder={t("org.invitePh")} className={cn(input, "sm:w-72")} required />
                <Button type="submit" size="sm" variant="outline" disabled={invite.isPending || used >= o.seats}>{t("org.invite")}</Button>
              </div>
              {msg && <div className={cn("rounded-md border px-3 py-2 text-xs", msg.ok ? "border-positive/40 bg-positive/10" : "border-negative/40 bg-negative/10")}>{msg.text}</div>}
              <div className="pt-1">
                <button type="button" onClick={() => { if (confirm(t("org.closeConfirm", { name: o.name }))) close.mutate() }} disabled={close.isPending} className="text-xs text-muted-foreground hover:text-negative">{t("org.close")}</button>
              </div>
            </form>
          )}
        </div>
      )}
    </Section>
  )
}
