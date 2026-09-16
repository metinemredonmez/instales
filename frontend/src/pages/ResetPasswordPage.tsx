import { useState, type FormEvent } from "react"
import { Link, useNavigate, useSearchParams } from "react-router-dom"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import { PublicFrame } from "@/components/layout/PublicFrame"

/** /reset?token=… — from the "forgot password" e-mail. Works signed-out; a successful reset adopts the new session. */
export function ResetPasswordPage() {
  const { t } = useI18n()
  const { resetPassword } = useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const token = params.get("token") ?? ""
  const [pw, setPw] = useState("")
  const [pw2, setPw2] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [busy, setBusy] = useState(false)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (pw !== pw2) return setError(t("account.mismatch"))
    setBusy(true)
    try {
      await resetPassword(token, pw)
      setDone(true)
      window.setTimeout(() => navigate("/", { replace: true }), 1200)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const input = "h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring/40"
  return (
    <PublicFrame title={t("reset.title")} sub={t("reset.sub")}>
      {!token ? (
        <div className="space-y-4">
          <div className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-sm">{t("reset.noToken")}</div>
          <Link to="/" className="block text-center text-xs text-muted-foreground hover:text-foreground">{t("login.back")}</Link>
        </div>
      ) : done ? (
        <div className="rounded-md border border-positive/40 bg-positive/10 px-3 py-2 text-sm">{t("reset.done")}</div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("account.new")}</div><input type="password" autoComplete="new-password" minLength={8} maxLength={128} value={pw} onChange={(e) => setPw(e.target.value)} className={input} required autoFocus /></label>
          <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("account.repeat")}</div><input type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} className={input} required /></label>
          <div className="text-[11px] text-muted-foreground">{t("account.policy")}</div>
          {error && <div className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-sm">{error}</div>}
          <Button type="submit" className="w-full" disabled={busy}>{busy ? "…" : t("reset.submit")}</Button>
          <Link to="/" className="block text-center text-xs text-muted-foreground hover:text-foreground">{t("login.back")}</Link>
        </form>
      )}
    </PublicFrame>
  )
}
