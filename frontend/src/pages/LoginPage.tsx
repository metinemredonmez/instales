import { useState, type FormEvent } from "react"
import { Gauge, Radar, TrendingDown } from "lucide-react"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { GradientWordmark, Mark } from "@/components/layout/Brand"

export function LoginPage() {
  const { login, register } = useAuth()
  const { lang, setLang, t } = useI18n()
  const [mode, setMode] = useState<"login" | "register">("login")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [name, setName] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      mode === "login" ? await login(email, password) : await register(email, password, name)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const features = [
    [t("login.f1.title"), t("login.f1.desc")],
    [t("login.f2.title"), t("login.f2.desc")],
    [t("login.f3.title"), t("login.f3.desc")],
  ]

  return (
    <div className="grid min-h-dvh bg-background text-foreground lg:grid-cols-[1.15fr_1fr]">
      <aside className="relative hidden overflow-hidden border-r border-border/60 p-12 lg:flex lg:flex-col" style={{ background: "linear-gradient(135deg, var(--background) 0%, color-mix(in oklab, var(--primary) 18%, var(--background)) 100%)" }}>
        {/* faint grid + glow, so the panel reads as a "lens" rather than a flat wall */}
        <div className="pointer-events-none absolute inset-0 opacity-[0.35] [background-image:linear-gradient(to_right,var(--border)_1px,transparent_1px),linear-gradient(to_bottom,var(--border)_1px,transparent_1px)] [background-size:48px_48px] [mask-image:radial-gradient(ellipse_at_30%_40%,black_20%,transparent_75%)]" />
        <div className="pointer-events-none absolute -left-32 top-1/3 size-[520px] rounded-full bg-primary/20 blur-3xl" />
        <div className="relative flex items-center gap-3">
          <Mark className="size-9" />
          <span className="text-[11px] font-semibold uppercase tracking-[0.35em] text-muted-foreground">Istanbul · Estd 2027</span>
        </div>
        <div className="relative my-auto max-w-xl">
          <GradientWordmark className="w-full max-w-lg" />
          <div className="mt-4 text-[13px] font-semibold uppercase tracking-[0.42em] text-muted-foreground">See where smart money moves.</div>
          <h1 className="mt-12 text-4xl font-semibold leading-[1.1] tracking-tight xl:text-5xl">
            {lang === "tr" ? <>Profesyonel para <span className="text-primary">nereye</span> gidiyor?</> : <>See <span className="text-primary">where</span> smart money moves.</>}
          </h1>
          <p className="mt-5 max-w-md text-[15px] leading-relaxed text-muted-foreground">{t("login.pitch")}</p>
          <ul className="mt-10 grid gap-3 sm:grid-cols-3">
            {features.map(([ti, d], i) => {
              const Icon = [Radar, Gauge, TrendingDown][i]
              return (
                <li key={ti} className="rounded-lg border border-border/70 bg-card/60 p-3.5 backdrop-blur">
                  <Icon className="size-4 text-primary" />
                  <div className="mt-2 text-sm font-medium leading-tight">{ti}</div>
                  <div className="mt-1 text-xs leading-relaxed text-muted-foreground">{d}</div>
                </li>
              )
            })}
          </ul>
        </div>
        <div className="relative flex items-center gap-4 text-xs text-muted-foreground">
          <span>{t("login.disclaimer")}</span>
          <span className="ml-auto inline-flex items-center gap-1.5 rounded-sm border border-border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider">KAP · SEC 13F</span>
        </div>
      </aside>

      <main className="relative flex items-center justify-center p-6">
        <div className="absolute right-5 top-5 flex overflow-hidden rounded-md border border-border text-[11px] font-semibold" role="radiogroup" aria-label={t("lang.label")}>
          {(["tr", "en"] as const).map((l) => (
            <button key={l} type="button" role="radio" aria-checked={lang === l} onClick={() => setLang(l)} className={cn("px-2.5 py-1 uppercase", lang === l ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground")}>{l}</button>
          ))}
        </div>
        <form onSubmit={submit} className="w-full max-w-sm space-y-5 rounded-xl border border-border bg-card p-7 shadow-xl shadow-black/10">
          <div className="flex flex-col items-center gap-3 pb-1 lg:hidden">
            <GradientWordmark className="w-56" />
            <div className="text-[10px] font-semibold uppercase tracking-[0.35em] text-muted-foreground">See where smart money moves.</div>
          </div>
          <div className="hidden items-center gap-3 lg:flex">
            <Mark className="size-9" />
            <div>
              <div className="text-base font-semibold leading-tight">{mode === "login" ? t("login.welcome") : t("login.createAccount")}</div>
              <div className="text-xs text-muted-foreground">{t("login.welcomeSub")}</div>
            </div>
          </div>
          <div className="flex rounded-md border border-border p-0.5 text-sm">
            {(["login", "register"] as const).map((m) => (
              <button type="button" key={m} onClick={() => { setMode(m); setError(null) }} className={cn("flex-1 rounded-[5px] py-1.5 font-medium", mode === m ? "bg-secondary" : "text-muted-foreground")}>
                {m === "login" ? t("login.signIn") : t("login.signUp")}
              </button>
            ))}
          </div>
          {mode === "register" && <Field label={t("field.name")} value={name} onChange={setName} autoComplete="name" />}
          <Field label={t("field.email")} type="email" value={email} onChange={setEmail} autoComplete="email" required />
          <Field label={t("field.password")} type="password" value={password} onChange={setPassword} autoComplete={mode === "login" ? "current-password" : "new-password"} required minLength={8} hint={mode === "register" ? t("login.min8") : undefined} />
          {error && <div className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-sm">{error}</div>}
          <Button type="submit" className="w-full" disabled={busy}>{busy ? "…" : mode === "login" ? t("login.signIn") : t("login.createAccount")}</Button>
          <p className="text-center text-xs text-muted-foreground">{t("login.invite")}</p>
        </form>
      </main>
    </div>
  )
}

function Field({ label, value, onChange, hint, ...rest }: { label: string; value: string; onChange: (v: string) => void; hint?: string } & Omit<React.InputHTMLAttributes<HTMLInputElement>, "onChange" | "value">) {
  return (
    <label className="block">
      <div className="mb-1 text-xs text-muted-foreground">{label}</div>
      <input value={value} onChange={(e) => onChange(e.target.value)} {...rest} className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring/40" />
      {hint && <div className="mt-1 text-[11px] text-muted-foreground">{hint}</div>}
    </label>
  )
}
