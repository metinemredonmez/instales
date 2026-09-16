import { useMutation } from "@tanstack/react-query"
import { useState, type FormEvent } from "react"
import { KeyRound, LogOut } from "lucide-react"
import { api } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { Section } from "@/components/layout/Section"
import { Button } from "@/components/ui/button"

/** Password change (revokes every other session) and "log out everywhere". */
export function AccountSecurityCard() {
  const { t } = useI18n()
  const { adoptSession, logout } = useAuth()
  const [cur, setCur] = useState("")
  const [nw, setNw] = useState("")
  const [nw2, setNw2] = useState("")
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const change = useMutation({
    mutationFn: () => api.changePassword(cur, nw),
    onSuccess: (s) => { adoptSession(s); setCur(""); setNw(""); setNw2(""); setMsg({ ok: true, text: t("account.changed") }) },
    onError: (e) => setMsg({ ok: false, text: (e as Error).message }),
  })
  const all = useMutation({ mutationFn: api.logoutAll, onSuccess: () => logout() })
  const submit = (e: FormEvent) => { e.preventDefault(); setMsg(null); if (nw !== nw2) return setMsg({ ok: false, text: t("account.mismatch") }); change.mutate() }
  const input = "h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring/40"
  return (
    <Section title={t("account.title")} hint={t("account.hint")}>
      <form onSubmit={submit} className="space-y-3 p-4 text-sm">
        <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("account.current")}</div><input type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} className={input} required /></label>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("account.new")}</div><input type="password" autoComplete="new-password" minLength={8} maxLength={128} value={nw} onChange={(e) => setNw(e.target.value)} className={input} required /></label>
          <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("account.repeat")}</div><input type="password" autoComplete="new-password" value={nw2} onChange={(e) => setNw2(e.target.value)} className={input} required /></label>
        </div>
        <div className="text-[11px] text-muted-foreground">{t("account.policy")}</div>
        {msg && <div className={`rounded-md border px-3 py-2 text-xs ${msg.ok ? "border-positive/40 bg-positive/10" : "border-negative/40 bg-negative/10"}`}>{msg.text}</div>}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" size="sm" disabled={change.isPending}><KeyRound className="size-4" /> {t("account.change")}</Button>
          <Button type="button" size="sm" variant="outline" onClick={() => all.mutate()} disabled={all.isPending}><LogOut className="size-4" /> {t("account.logoutAll")}</Button>
        </div>
      </form>
    </Section>
  )
}
