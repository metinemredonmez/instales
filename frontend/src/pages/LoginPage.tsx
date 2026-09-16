import { useState, type FormEvent } from "react"
import { Radar } from "lucide-react"
import { useAuth } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { Lockup, Mark } from "@/components/layout/Brand"

export function LoginPage() {
  const { login, register } = useAuth()
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

  return (
    <div className="grid min-h-dvh bg-background text-foreground lg:grid-cols-[1.1fr_1fr]">
      <aside className="relative hidden overflow-hidden border-r border-border bg-card p-10 lg:flex lg:flex-col">
        <Mark className="size-9 rounded-lg" />
        <div className="my-auto max-w-md">
          <Lockup className="mb-10 max-w-sm" />
          <h1 className="text-4xl font-semibold leading-tight tracking-tight">Profesyonel para <span className="text-primary">nereye</span> gidiyor?</h1>
          <p className="mt-4 text-muted-foreground">Fonların hangi hisseleri topladığını, azalttığını, yeni pozisyon açtığını ve terk ettiğini tek ekrandan takip et. Her rakam KAP kaynağına kadar izlenebilir.</p>
          <ul className="mt-8 space-y-3 text-sm">
            {[
              ["Live KAP Radar", "Bildirim geldiği an normalize edilmiş akış"],
              ["Smart Money & Konsensüs Skoru", "Deterministic, açıklanabilir — 'Neden 87?'"],
              ["Accumulation / Divergence", "Fiyat düşerken toplayan fonları yakala"],
            ].map(([t, d]) => (
              <li key={t} className="flex gap-3">
                <Radar className="mt-0.5 size-4 shrink-0 text-positive" />
                <div><div className="font-medium">{t}</div><div className="text-muted-foreground">{d}</div></div>
              </li>
            ))}
          </ul>
        </div>
        <div className="text-xs text-muted-foreground">Veri sunumudur, yatırım tavsiyesi değildir.</div>
        <div className="pointer-events-none absolute -right-24 -top-24 size-96 rounded-full bg-primary/10 blur-3xl" />
      </aside>

      <main className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="w-full max-w-sm space-y-5">
          <div className="flex flex-col items-center gap-4 pb-2 lg:hidden">
            <Mark className="size-12 rounded-xl" />
            <Lockup className="max-w-[260px]" />
          </div>
          <div className="flex rounded-md border border-border p-0.5 text-sm">
            {(["login", "register"] as const).map((m) => (
              <button type="button" key={m} onClick={() => { setMode(m); setError(null) }} className={cn("flex-1 rounded-[5px] py-1.5 font-medium", mode === m ? "bg-secondary" : "text-muted-foreground")}>
                {m === "login" ? "Giriş yap" : "Kayıt ol"}
              </button>
            ))}
          </div>
          {mode === "register" && <Field label="Ad" value={name} onChange={setName} autoComplete="name" />}
          <Field label="E-posta" type="email" value={email} onChange={setEmail} autoComplete="email" required />
          <Field label="Şifre" type="password" value={password} onChange={setPassword} autoComplete={mode === "login" ? "current-password" : "new-password"} required minLength={8} hint={mode === "register" ? "En az 8 karakter" : undefined} />
          {error && <div className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-sm">{error}</div>}
          <Button type="submit" className="w-full" disabled={busy}>{busy ? "…" : mode === "login" ? "Giriş yap" : "Hesap oluştur"}</Button>
          <p className="text-center text-xs text-muted-foreground">Beta süresince kayıt açık ve ücretsizdir.</p>
        </form>
      </main>
    </div>
  )
}

function Field({ label, value, onChange, hint, ...rest }: { label: string; value: string; onChange: (v: string) => void; hint?: string } & Omit<React.InputHTMLAttributes<HTMLInputElement>, "onChange" | "value">) {
  return (
    <label className="block">
      <div className="mb-1 text-xs text-muted-foreground">{label}</div>
      <input value={value} onChange={(e) => onChange(e.target.value)} {...rest} className="h-10 w-full rounded-md border border-input bg-card px-3 text-sm outline-none focus:ring-2 focus:ring-ring/40" />
      {hint && <div className="mt-1 text-[11px] text-muted-foreground">{hint}</div>}
    </label>
  )
}
