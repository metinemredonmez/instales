import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { useState, type FormEvent } from "react"
import { Trash2 } from "lucide-react"
import { api } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { Section } from "@/components/layout/Section"
import { Flow, ScorePill } from "@/components/domain/badges"
import { Button } from "@/components/ui/button"

export function WatchlistPage() {
  const { market } = useMarket()
  const qc = useQueryClient()
  const wl = useQuery({ queryKey: ["watchlist"], queryFn: api.watchlist })
  const [ref, setRef] = useState("")
  const [err, setErr] = useState<string | null>(null)
  const add = useMutation({
    mutationFn: (r: string) => (r.length === 3 ? api.addWatch({ fund_code: r, market }) : api.addWatch({ symbol: r, market })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["watchlist"] }); setRef(""); setErr(null) },
    onError: (e) => setErr((e as Error).message),
  })
  const remove = useMutation({ mutationFn: api.removeWatch, onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }) })
  const submit = (e: FormEvent) => { e.preventDefault(); if (ref.trim()) add.mutate(ref.trim().toUpperCase()) }

  const stocks = wl.data?.filter((i) => i.kind === "stock") ?? []
  const funds = wl.data?.filter((i) => i.kind === "fund") ?? []

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-2xl font-semibold tracking-tight">Takip listem</h1><p className="text-sm text-muted-foreground">Hisse ve fonlar; skorlar son hesaplamadan.</p></div>
        <form onSubmit={submit} className="flex gap-2">
          <input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="ASELS veya TMV" className="h-9 w-40 rounded-md border border-input bg-card px-3 text-sm outline-none focus:ring-2 focus:ring-ring/40" />
          <Button type="submit" size="sm" disabled={add.isPending}>Ekle</Button>
        </form>
      </div>
      {err && <div className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-sm">{err}</div>}
      <Section title="Hisseler" hint={`${stocks.length}`}>
        {stocks.length === 0 && <div className="px-4 py-6 text-sm text-muted-foreground">Henüz hisse eklenmedi.</div>}
        {stocks.length > 0 && (
          <table className="w-full text-sm">
            <thead className="text-[11px] uppercase tracking-wider text-muted-foreground"><tr className="border-b border-border/60"><th className="px-4 py-2 text-left font-medium">Hisse</th><th className="px-2 py-2 text-right font-medium">Smart Money</th><th className="px-2 py-2 text-right font-medium">Fon ↑/↓</th><th className="px-2 py-2 text-right font-medium">Net akış</th><th className="w-10" /></tr></thead>
            <tbody>
              {stocks.map((i) => (
                <tr key={i.id} className="border-b border-border/40 last:border-0 hover:bg-accent/40">
                  <td className="px-4 py-2"><Link to={`/stocks/${i.ref}`} className="font-semibold hover:underline">{i.ref}</Link></td>
                  <td className="px-2 py-2 text-right"><ScorePill value={i.smart_money_score} /></td>
                  <td className="num px-2 py-2 text-right"><span className="text-positive">{i.funds_increasing ?? "—"}</span> / <span className="text-negative">{i.funds_reducing ?? "—"}</span></td>
                  <td className="px-2 py-2 text-right"><Flow value={i.net_flow_value} market={market} /></td>
                  <td className="px-2 py-2 text-right"><button onClick={() => remove.mutate(i.id)} className="text-muted-foreground hover:text-negative" aria-label="Kaldır"><Trash2 className="size-4" /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
      <Section title="Fonlar" hint={`${funds.length}`}>
        {funds.length === 0 && <div className="px-4 py-6 text-sm text-muted-foreground">Henüz fon eklenmedi.</div>}
        <ul className="divide-y divide-border/60">
          {funds.map((i) => (
            <li key={i.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
              <Link to={`/funds/${i.ref}`} className="font-mono font-semibold hover:underline">{i.ref}</Link>
              <span className="truncate text-muted-foreground">{i.name}</span>
              <button onClick={() => remove.mutate(i.id)} className="ml-auto text-muted-foreground hover:text-negative" aria-label="Kaldır"><Trash2 className="size-4" /></button>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  )
}
