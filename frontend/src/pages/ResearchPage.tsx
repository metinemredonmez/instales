import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Sparkles } from "lucide-react"
import { api } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { Button } from "@/components/ui/button"
import { Section } from "@/components/layout/Section"

const EXAMPLES = [
  "Son 30 günde fiyatı düşerken fonların toplamaya devam ettiği hisseler hangileri?",
  "TMV son dönemde hangi hisselere yeni girdi, hangilerinden çıktı?",
  "ASELS'te kurumsal konsensüs neden bu seviyede? Kaynaklarıyla açıkla.",
]

export function ResearchPage() {
  const { market } = useMarket()
  const [q, setQ] = useState("")
  const m = useMutation({ mutationFn: (question: string) => api.research(question, market) })

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight"><Sparkles className="size-5 text-primary" /> AI Research</h1>
        <p className="text-sm text-muted-foreground">Model sayı üretmez; sadece InstiLens araçlarını çağırır. Her cevabın altında hangi araçların çağrıldığını görürsün.</p>
      </div>
      <form
        onSubmit={(e) => { e.preventDefault(); if (q.trim()) m.mutate(q.trim()) }}
        className="rounded-lg border border-border bg-card p-3"
      >
        <textarea value={q} onChange={(e) => setQ(e.target.value)} rows={3} placeholder="Sorunu yaz…" className="w-full resize-none bg-transparent text-sm outline-none placeholder:text-muted-foreground" />
        <div className="flex flex-wrap items-center gap-2">
          {EXAMPLES.map((ex) => <button type="button" key={ex} onClick={() => setQ(ex)} className="rounded-sm border border-border px-2 py-1 text-left text-[11px] text-muted-foreground hover:text-foreground">{ex}</button>)}
          <Button type="submit" size="sm" className="ml-auto" disabled={m.isPending}>{m.isPending ? "Araştırıyor…" : "Sor"}</Button>
        </div>
      </form>
      {m.isError && (
        <div className="rounded-lg border border-negative/40 bg-negative/10 p-4 text-sm">
          <b>Hata:</b> {(m.error as Error).message}
          <div className="mt-1 text-xs text-muted-foreground">Backend'de <code>ANTHROPIC_API_KEY</code> tanımlı mı?</div>
        </div>
      )}
      {m.data && (
        <>
          <Section title="Cevap" hint={m.data.model}>
            <div className="whitespace-pre-wrap px-4 py-3 text-sm leading-relaxed">{m.data.answer}</div>
          </Section>
          <Section title="Kaynak: çağrılan araçlar" hint={`${m.data.tool_calls.length}`}>
            <ul className="divide-y divide-border/60 text-xs">
              {m.data.tool_calls.map((t, i) => (
                <li key={i} className="px-4 py-2">
                  <span className="font-mono font-semibold">{t.name}</span> <span className="text-muted-foreground">{JSON.stringify(t.input)}</span>
                  <pre className="mt-1 truncate text-muted-foreground/70">{t.output_preview}</pre>
                </li>
              ))}
            </ul>
          </Section>
        </>
      )}
    </div>
  )
}
