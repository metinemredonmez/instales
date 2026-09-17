import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, type AdminUser } from "@/lib/api"
import { useI18n, type T } from "@/lib/i18n"
import { fmtDateTime } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import type { Key } from "@/i18n/tr"

/** The account's own plan source as users.plan_source holds it: "stripe" (a paid subscription) or "manual" (admin-granted); null on FREE. */
const SOURCE: Record<string, Key> = { stripe: "plan.source.stripe", manual: "plan.source.manual", own: "plan.source.own", org: "plan.source.org" }
const dateInput = "num rounded border border-input bg-background px-1 py-0.5 text-xs disabled:opacity-40"

/**
 * A manual grant can be dated: one with a source of "manual", or a plan set before grants were recorded (no source,
 * not FREE — the API adopts it as manual on the first re-date). A Stripe subscription is dated by Stripe.
 */
export const canDate = (u: AdminUser) => u.plan !== "FREE" && (u.plan_source === "manual" || u.plan_source == null)

/**
 * The expiry field commits on blur or Enter, not on every keystroke (a year typed digit by digit is a run of valid
 * dates, each of which would be a PATCH). An emptied field asks before lifting the expiry; unchanged is no request.
 */
function UntilField({ u, t, onCommit }: { u: AdminUser; t: T; onCommit: (until: string | null) => void }) {
  const stored = u.plan_until?.slice(0, 10) ?? ""
  const [value, setValue] = useState(stored)
  useEffect(() => setValue(stored), [stored])
  const commit = () => {
    if (value === stored) return
    if (value === "") { if (confirm(t("admin.users.clearUntil", { email: u.email }))) onCommit(null); else setValue(stored); return }
    onCommit(value)
  }
  return <input type="date" value={value} disabled={!canDate(u)} onChange={(e) => setValue(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur() }} aria-label={`${t("admin.users.manualUntil")}: ${u.email}`} className={dateInput} />
}

export function AdminUsersPage() {
  const { t } = useI18n()
  const qc = useQueryClient()
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: api.adminUsers })
  const waitlist = useQuery({ queryKey: ["admin", "waitlist"], queryFn: api.adminWaitlist })
  const patch = useMutation({ mutationFn: ({ id, body }: { id: number; body: Parameters<typeof api.adminPatchUser>[1] }) => api.adminPatchUser(id, body), onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "users"] }) })
  const th = "px-2 py-2 text-left font-medium"
  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("admin.users.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("admin.users.sub")}</p>
      </div>
      <Section title={t("admin.users.title")} hint={`${users.data?.length ?? 0}`}>
        {/* The API's 400s (a date in the past, nothing manual to date, an account Stripe manages) would otherwise vanish behind a control that snaps back. */}
        {patch.isError && <div role="alert" className="mx-4 mt-3 rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-xs">{(patch.error as Error).message}</div>}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
              <tr className="border-b border-border/60"><th className="px-4 py-2 text-left font-medium">{t("field.email")}</th><th className={th}>{t("field.name")}</th><th className={th}>{t("field.plan")}</th><th className={th}>{t("admin.users.planSource")}</th><th className={th}>{t("admin.users.manualUntil")}</th><th className={th}>{t("field.role")}</th><th className={th}>{t("admin.users.lastLogin")}</th><th className="px-4 py-2 text-right font-medium">{t("field.active")}</th></tr>
            </thead>
            <tbody>
              {users.data?.map((u) => (
                <tr key={u.id} className="border-b border-border/40 last:border-0">
                  <td className="px-4 py-2">{u.email}</td><td className="px-2 py-2">{u.name}</td>
                  {/* The select is the account's own row; a Stripe subscription is changed in Stripe (the API answers 400 here). The badge shows the plan in effect when it differs (an org seat lifts it, an expired grant lowers it). */}
                  <td className="px-2 py-2">
                    <select value={u.plan} disabled={u.plan_source === "stripe"} title={u.plan_source === "stripe" ? t("admin.users.stripeManaged") : undefined} onChange={(e) => patch.mutate({ id: u.id, body: { plan: e.target.value as "FREE" } })} className="rounded border border-input bg-background px-1 py-0.5 text-xs disabled:opacity-60">{["FREE", "PRO", "PRO_PLUS"].map((p) => <option key={p}>{p}</option>)}</select>
                    {u.effective_plan && u.effective_plan !== u.plan && <span className="ml-1 rounded-sm border border-primary/40 bg-primary/10 px-1 py-px text-[10px] text-primary" title={`${t("admin.users.effective")}${u.effective_source ? ` · ${SOURCE[u.effective_source] ? t(SOURCE[u.effective_source]) : u.effective_source}` : ""}`}>{u.effective_plan}</span>}
                  </td>
                  <td className="px-2 py-2 text-xs text-muted-foreground">{u.plan_source ? (SOURCE[u.plan_source] ? t(SOURCE[u.plan_source]) : u.plan_source) : "—"}</td>
                  {/* Dates an admin-granted plan (users.plan_until); an emptied field lifts the expiry after a confirmation. */}
                  <td className="px-2 py-2"><UntilField u={u} t={t} onCommit={(until) => patch.mutate({ id: u.id, body: { plan_until: until } })} /></td>
                  <td className="px-2 py-2"><select value={u.role} onChange={(e) => patch.mutate({ id: u.id, body: { role: e.target.value as "USER" } })} className="rounded border border-input bg-background px-1 py-0.5 text-xs">{["USER", "ADMIN"].map((p) => <option key={p}>{p}</option>)}</select></td>
                  <td className="num px-2 py-2 text-xs text-muted-foreground">{u.last_login_at ? fmtDateTime(u.last_login_at) : "—"}</td>
                  <td className="px-4 py-2 text-right"><input type="checkbox" checked={u.is_active} onChange={(e) => patch.mutate({ id: u.id, body: { is_active: e.target.checked } })} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
      <Section title={t("admin.waitlist.title")} hint={`${waitlist.data?.length ?? 0} · ${t("admin.waitlist.hint")}`}>
        <ul className="divide-y divide-border/60 text-sm">
          {waitlist.data?.map((w) => (
            <li key={w.id} className="flex flex-wrap items-center gap-3 px-4 py-2">
              <span className="font-medium">{w.email}</span>
              {w.name && <span className="text-muted-foreground">{w.name}</span>}
              <span className="rounded-sm bg-muted px-1 py-px font-mono text-[10px] uppercase text-muted-foreground">{w.lang}</span>
              {w.source && <span className="truncate text-xs text-muted-foreground">{w.source}</span>}
              <span className="num ml-auto text-xs text-muted-foreground">{fmtDateTime(w.created_at)}</span>
            </li>
          ))}
          {waitlist.data?.length === 0 && <li className="px-4 py-6 text-muted-foreground">{t("common.none")}</li>}
        </ul>
      </Section>
    </>
  )
}
