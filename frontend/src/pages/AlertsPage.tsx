import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { useState, type FormEvent } from "react"
import { Trash2 } from "lucide-react"
import { api } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { fmtDateTime } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const RULE_LABEL: Record<string, string> = {
  NEW_FUND_POSITION: "Yeni fon pozisyonu",
  FUND_EXIT: "Fon çıkışı",
  KAP_TRANSACTION: "KAP işlem bildirimi",
  SCORE_ABOVE: "Smart Money Score eşiği",
  SIGNAL: "Sinyal",
  FUND_ACTIVITY: "Fon aktivitesi (NEW/EXIT)",
}

export function AlertsPage() {
  const { market } = useMarket()
  const qc = useQueryClient()
  const rules = useQuery({ queryKey: ["rules"], queryFn: api.rules })
  const notes = useQuery({ queryKey: ["notifications"], queryFn: api.notifications, refetchInterval: 30_000 })
  const [ref, setRef] = useState("")
  const [type, setType] = useState("NEW_FUND_POSITION")
  const [threshold, setThreshold] = useState(80)
  const [err, setErr] = useState<string | null>(null)
  const invalidate = () => { qc.invalidateQueries({ queryKey: ["rules"] }); qc.invalidateQueries({ queryKey: ["notifications"] }) }
  const add = useMutation({
    mutationFn: () => {
      const r = ref.trim().toUpperCase()
      const subject = r.length === 3 ? { fund_code: r } : { symbol: r }
      return api.addRule({ ...subject, market, rule_type: type, params: type === "SCORE_ABOVE" ? { threshold } : {} })
    },
    onSuccess: () => { invalidate(); setRef(""); setErr(null) },
    onError: (e) => setErr((e as Error).message),
  })
  const remove = useMutation({ mutationFn: api.removeRule, onSuccess: invalidate })
  const evaluate = useMutation({ mutationFn: api.evaluateAlerts, onSuccess: invalidate })
  const markAll = useMutation({ mutationFn: () => api.markRead(), onSuccess: invalidate })
  const submit = (e: FormEvent) => { e.preventDefault(); if (ref.trim()) add.mutate() }
  const active = rules.data?.rules.filter((r) => r.is_active) ?? []
  const unread = notes.data?.filter((n) => !n.read_at).length ?? 0

  return (
    <div className="space-y-5">
      <div><h1 className="text-2xl font-semibold tracking-tight">Alarmlar</h1><p className="text-sm text-muted-foreground">Kurallar her hesaplamadan sonra değerlendirilir; aynı olay bir kez bildirilir. Bildirimler betimleyicidir, tavsiye değildir.</p></div>
      <div className="grid gap-5 lg:grid-cols-[380px_1fr]">
        <div className="space-y-5">
          <Section title="Yeni kural">
            <form onSubmit={submit} className="space-y-3 p-4 text-sm">
              <label className="block"><div className="mb-1 text-xs text-muted-foreground">Hisse (ASELS) veya fon (TMV)</div><input value={ref} onChange={(e) => setRef(e.target.value)} className="h-9 w-full rounded-md border border-input bg-background px-3 outline-none focus:ring-2 focus:ring-ring/40" /></label>
              <label className="block"><div className="mb-1 text-xs text-muted-foreground">Kural</div>
                <select value={type} onChange={(e) => setType(e.target.value)} className="h-9 w-full rounded-md border border-input bg-background px-2 outline-none">
                  {(rules.data?.rule_types ?? Object.keys(RULE_LABEL)).map((t) => <option key={t} value={t}>{RULE_LABEL[t] ?? t}</option>)}
                </select>
              </label>
              {type === "SCORE_ABOVE" && <label className="block"><div className="mb-1 text-xs text-muted-foreground">Eşik</div><input type="number" value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} className="num h-9 w-full rounded-md border border-input bg-background px-3 outline-none" /></label>}
              {err && <div className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-xs">{err}</div>}
              <Button type="submit" size="sm" className="w-full" disabled={add.isPending}>Kural ekle</Button>
            </form>
          </Section>
          <Section title="Aktif kurallar" hint={`${active.length}`}>
            <ul className="divide-y divide-border/60 text-sm">
              {active.map((r) => (
                <li key={r.id} className="flex items-center gap-3 px-4 py-2.5">
                  <Link to={r.symbol ? `/stocks/${r.symbol}` : `/funds/${r.fund_code}`} className="font-semibold hover:underline">{r.symbol ?? r.fund_code}</Link>
                  <span className="text-muted-foreground">{RULE_LABEL[r.rule_type] ?? r.rule_type}{r.rule_type === "SCORE_ABOVE" && ` ≥ ${String(r.params.threshold ?? 80)}`}</span>
                  <button onClick={() => remove.mutate(r.id)} className="ml-auto text-muted-foreground hover:text-negative" aria-label="Sil"><Trash2 className="size-4" /></button>
                </li>
              ))}
              {active.length === 0 && <li className="px-4 py-6 text-muted-foreground">Kural yok.</li>}
            </ul>
          </Section>
        </div>
        <Section title="Bildirimler" hint={unread ? `${unread} okunmamış` : undefined} right={
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={() => evaluate.mutate()} disabled={evaluate.isPending}>Şimdi değerlendir</Button>
            <Button variant="ghost" size="sm" onClick={() => markAll.mutate()} disabled={!unread}>Tümünü okundu yap</Button>
          </div>
        }>
          <ul className="divide-y divide-border/60">
            {notes.data?.map((n) => (
              <li key={n.id} className={cn("px-4 py-3", !n.read_at && "bg-primary/5")}>
                <div className="flex items-center gap-2">
                  {!n.read_at && <span className="size-1.5 rounded-full bg-primary" />}
                  <Link to={n.link ?? "#"} className="text-sm font-medium hover:underline">{n.title}</Link>
                  <span className="ml-auto num text-xs text-muted-foreground">{fmtDateTime(n.created_at)}</span>
                </div>
                <div className="mt-0.5 text-xs text-muted-foreground">{n.body}</div>
              </li>
            ))}
            {notes.data?.length === 0 && <li className="px-4 py-8 text-center text-sm text-muted-foreground">Henüz bildirim yok. Kural ekleyip "Şimdi değerlendir"e bas.</li>}
          </ul>
        </Section>
      </div>
    </div>
  )
}
