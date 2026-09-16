import { useQuery } from "@tanstack/react-query"
import { Link, useSearchParams } from "react-router-dom"
import { useState, type FormEvent } from "react"
import { api } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { Section } from "@/components/layout/Section"
import { ActivityBadge } from "@/components/domain/badges"
import { Button } from "@/components/ui/button"

export function ComparePage() {
  const { t } = useI18n()
  const [params, setParams] = useSearchParams()
  const a = params.get("a") ?? ""
  const b = params.get("b") ?? ""
  const [fa, setFa] = useState(a)
  const [fb, setFb] = useState(b)
  const q = useQuery({ queryKey: ["compare", a, b], queryFn: () => api.compare(a, b), enabled: !!a && !!b })
  const submit = (e: FormEvent) => { e.preventDefault(); setParams({ a: fa.trim().toUpperCase(), b: fb.trim().toUpperCase() }) }
  const d = q.data

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-2xl font-semibold tracking-tight">{t("compare.title")}</h1><p className="text-sm text-muted-foreground">{t("compare.sub")}</p></div>
        <form onSubmit={submit} className="flex items-center gap-2">
          <input value={fa} onChange={(e) => setFa(e.target.value)} placeholder="TMV" className="h-9 w-24 rounded-md border border-input bg-card px-3 font-mono text-sm outline-none focus:ring-2 focus:ring-ring/40" />
          <span className="text-muted-foreground">vs</span>
          <input value={fb} onChange={(e) => setFb(e.target.value)} placeholder="MAC" className="h-9 w-24 rounded-md border border-input bg-card px-3 font-mono text-sm outline-none focus:ring-2 focus:ring-ring/40" />
          <Button type="submit" size="sm">{t("nav.compare")}</Button>
        </form>
      </div>
      {q.isError && <div className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-sm">{t("fund.notFound")}</div>}
      {d && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label={t("compare.common")} value={d.common.length} />
            <Stat label={t("compare.overlap")} value={`${d.overlap_pct}%`} />
            <Stat label={t("compare.only", { f: d.a.code })} value={d.only_a.length} />
            <Stat label={t("compare.only", { f: d.b.code })} value={d.only_b.length} />
          </div>
          <div className="grid gap-5 lg:grid-cols-3">
            <List title={t("compare.bothIncreasing")} items={d.both_increasing} tone="pos" />
            <List title={t("compare.bothReducing")} items={d.both_reducing} tone="neg" />
            <Section title={t("compare.opposite")} hint={`${d.opposite.length}`}>
              <ul className="divide-y divide-border/60 text-sm">
                {d.opposite.map((o) => <li key={o.symbol} className="flex items-center gap-3 px-4 py-2"><Link to={`/stocks/${o.symbol}`} className="w-16 font-semibold hover:underline">{o.symbol}</Link><span className="font-mono text-xs">{d.a.code}</span><ActivityBadge value={o.a} /><span className="font-mono text-xs">{d.b.code}</span><ActivityBadge value={o.b} /></li>)}
                {d.opposite.length === 0 && <li className="px-4 py-6 text-muted-foreground">{t("common.none")}</li>}
              </ul>
            </Section>
          </div>
          <Section title={t("compare.commonPositions")} hint={`${d.a.code} · ${d.b.code}`}>
            <table className="w-full text-sm">
              <thead className="text-[11px] uppercase tracking-wider text-muted-foreground"><tr className="border-b border-border/60"><th className="px-4 py-2 text-left font-medium">{t("common.stock")}</th><th className="px-2 py-2 text-right font-medium">{d.a.code} {t("common.weight").toLowerCase()}</th><th className="px-2 py-2 text-left font-medium">{t("common.move").toLowerCase()}</th><th className="px-2 py-2 text-right font-medium">{d.b.code} {t("common.weight").toLowerCase()}</th><th className="px-4 py-2 text-left font-medium">{t("common.move").toLowerCase()}</th></tr></thead>
              <tbody>
                {d.common.map((c) => (
                  <tr key={c.symbol} className="border-b border-border/40 last:border-0 hover:bg-accent/40">
                    <td className="px-4 py-2"><Link to={`/stocks/${c.symbol}`} className="font-semibold hover:underline">{c.symbol}</Link></td>
                    <td className="num px-2 py-2 text-right">{c.a_weight_pct?.toFixed(1) ?? "—"}%</td><td className="px-2 py-2">{c.a_move && <ActivityBadge value={c.a_move} />}</td>
                    <td className="num px-2 py-2 text-right">{c.b_weight_pct?.toFixed(1) ?? "—"}%</td><td className="px-4 py-2">{c.b_move && <ActivityBadge value={c.b_move} />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>
        </>
      )}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="rounded-lg border border-border bg-card px-4 py-3"><div className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</div><div className="num mt-1 text-2xl font-semibold">{value}</div></div>
}

function List({ title, items, tone }: { title: string; items: string[]; tone: "pos" | "neg" }) {
  const { t } = useI18n()
  return (
    <Section title={title} hint={`${items.length}`}>
      <ul className="flex flex-wrap gap-1.5 p-4">
        {items.map((s) => <Link key={s} to={`/stocks/${s}`} className={`rounded-sm border px-2 py-0.5 text-sm font-semibold ${tone === "pos" ? "border-positive/40 text-positive" : "border-negative/40 text-negative"}`}>{s}</Link>)}
        {items.length === 0 && <span className="text-sm text-muted-foreground">{t("common.none")}</span>}
      </ul>
    </Section>
  )
}
