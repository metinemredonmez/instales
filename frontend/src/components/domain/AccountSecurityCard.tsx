import { useMutation } from "@tanstack/react-query"
import { useEffect, useState, type FormEvent } from "react"
import { KeyRound, LogOut, ShieldCheck, ShieldOff } from "lucide-react"
import QRCode from "qrcode"
import { api, type MfaSetup } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { Section } from "@/components/layout/Section"
import { Button } from "@/components/ui/button"

const input = "h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring/40"

/** Password change (revokes every other session), "log out everywhere", and TOTP two-step verification for admins. */
export function AccountSecurityCard() {
  const { t } = useI18n()
  const { user, adoptSession, logout } = useAuth()
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
      <EmailVerifySection />
      {user?.role === "ADMIN" && <MfaSection />}
    </Section>
  )
}

/** E-mail verification status + resend. Login is never blocked on it; it is here so the warning in the header can be cleared. */
function EmailVerifySection() {
  const { t } = useI18n()
  const { user, refreshUser } = useAuth()
  const [msg, setMsg] = useState<string | null>(null)
  const resend = useMutation({
    mutationFn: api.verifyResend,
    onSuccess: (r) => { setMsg(r.email_verified ? t("emailverify.already") : r.sent ? t("emailverify.sent", { email: user?.email ?? "" }) : t("emailverify.noSmtp")); refreshUser().catch(() => {}) },
    onError: (e) => setMsg((e as Error).message),
  })
  const ok = user?.email_verified === true
  return (
    <div className="border-t border-border/60 p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{t("emailverify.title")}</span>
        <span className={`rounded-sm border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${ok ? "border-positive/40 text-positive" : "border-warning/40 text-warning"}`}>{ok ? t("emailverify.ok") : t("emailverify.pending")}</span>
        <span className="text-xs text-muted-foreground">{user?.email}</span>
      </div>
      {!ok && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" variant="outline" onClick={() => { setMsg(null); resend.mutate() }} disabled={resend.isPending}>{t("emailverify.send")}</Button>
          <span className="text-[11px] text-muted-foreground">{t("emailverify.howto")}</span>
        </div>
      )}
      {msg && <div className="mt-2 text-xs text-muted-foreground">{msg}</div>}
    </div>
  )
}

/**
 * TOTP enrolment: the server hands out a secret + otpauth URI; the QR is drawn client-side so the secret never
 * leaves this page. Enabling and disabling both require a current code.
 */
function MfaSection() {
  const { t } = useI18n()
  const { user, refreshUser } = useAuth()
  const [enabled, setEnabled] = useState<boolean>(user?.mfa_enabled === true)
  useEffect(() => { if (user?.mfa_enabled !== undefined) setEnabled(user.mfa_enabled) }, [user?.mfa_enabled])
  const [setup, setSetup] = useState<MfaSetup | null>(null)
  const [qr, setQr] = useState<string | null>(null)
  const [code, setCode] = useState("")
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  useEffect(() => {
    if (!setup) { setQr(null); return }
    let alive = true
    QRCode.toDataURL(setup.otpauth_uri, { margin: 1, width: 176, errorCorrectionLevel: "M" }).then((url) => alive && setQr(url)).catch(() => alive && setQr(null))
    return () => { alive = false }
  }, [setup])
  const start = useMutation({ mutationFn: api.mfaSetup, onSuccess: (s) => { setSetup(s); setMsg(null); setCode("") }, onError: (e) => setMsg({ ok: false, text: (e as Error).message }) })
  const enable = useMutation({
    mutationFn: () => api.mfaEnable(code.replace(/\s/g, "")),
    onSuccess: () => { setEnabled(true); setSetup(null); setCode(""); setMsg({ ok: true, text: t("mfa.enabled") }); refreshUser().catch(() => {}) },
    onError: (e) => setMsg({ ok: false, text: (e as Error).message }),
  })
  const disable = useMutation({
    mutationFn: () => api.mfaDisable(code.replace(/\s/g, "")),
    onSuccess: () => { setEnabled(false); setCode(""); setMsg({ ok: true, text: t("mfa.disabled") }); refreshUser().catch(() => {}) },
    onError: (e) => setMsg({ ok: false, text: (e as Error).message }),
  })
  const submit = (e: FormEvent) => { e.preventDefault(); setMsg(null); enabled ? disable.mutate() : enable.mutate() }
  const codeField = (
    <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("mfa.code")}</div><input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" maxLength={7} className={`num ${input} max-w-[160px]`} required /></label>
  )
  return (
    <div className="space-y-3 border-t border-border/60 p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        {enabled ? <ShieldCheck className="size-4 text-positive" /> : <ShieldOff className="size-4 text-muted-foreground" />}
        <span className="font-medium">{t("mfa.title")}</span>
        <span className="text-xs text-muted-foreground">{t("mfa.hint")}</span>
        <span className={`ml-auto rounded-sm border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider ${enabled ? "border-positive/40 bg-positive/10 text-positive" : "border-border text-muted-foreground"}`}>{enabled ? t("common.on") : t("common.off")}</span>
      </div>
      <div className="text-[11px] text-muted-foreground">{t("mfa.desc")}</div>
      {msg && <div className={`rounded-md border px-3 py-2 text-xs ${msg.ok ? "border-positive/40 bg-positive/10" : "border-negative/40 bg-negative/10"}`}>{msg.text}</div>}
      {enabled ? (
        <form onSubmit={submit} className="space-y-2">
          <div className="text-xs text-muted-foreground">{t("mfa.disableHint")}</div>
          {codeField}
          <Button type="submit" size="sm" variant="outline" disabled={disable.isPending}><ShieldOff className="size-4" /> {t("mfa.disable")}</Button>
        </form>
      ) : setup ? (
        <form onSubmit={submit} className="space-y-3">
          <div className="text-xs text-muted-foreground">{t("mfa.scan")}</div>
          <div className="flex flex-wrap items-start gap-4">
            {qr ? <img src={qr} alt="TOTP QR" width={176} height={176} className="rounded-md border border-border bg-white p-1" /> : <div className="grid size-44 place-items-center rounded-md border border-dashed border-border text-xs text-muted-foreground">…</div>}
            <div className="min-w-0 flex-1 space-y-2">
              <div className="break-all rounded-md bg-muted/60 px-2 py-1.5 font-mono text-xs tracking-wider">{setup.secret}</div>
              <div className="break-all text-[11px] text-muted-foreground"><a href={setup.otpauth_uri} className="hover:underline">{setup.otpauth_uri}</a></div>
              {codeField}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" size="sm" disabled={enable.isPending}><ShieldCheck className="size-4" /> {t("mfa.enable")}</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => { setSetup(null); setCode(""); setMsg(null) }}>{t("mfa.cancel")}</Button>
          </div>
        </form>
      ) : (
        <Button type="button" size="sm" variant="outline" onClick={() => start.mutate()} disabled={start.isPending}><ShieldCheck className="size-4" /> {t("mfa.setup")}</Button>
      )}
    </div>
  )
}
