import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Sparkles } from "lucide-react"
import { api } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import { Section } from "@/components/layout/Section"

const EXAMPLES = ["research.ex1", "research.ex2", "research.ex3"] as const

export function ResearchPage() {
  const { market } = useMarket()
  const { t } = useI18n()
  const [q, setQ] = useState("")
  const m = useMutation({ mutationFn: (question: string) => api.research(question, market) })

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight"><Sparkles className="size-5 text-primary" /> AI Research</h1>
        <p className="text-sm text-muted-foreground">{t("research.sub")}</p>
      </div>
      <form
        onSubmit={(e) => { e.preventDefault(); if (q.trim()) m.mutate(q.trim()) }}
        className="rounded-lg border border-border bg-card p-3"
      >
        <textarea value={q} onChange={(e) => setQ(e.target.value)} rows={3} placeholder={t("research.placeholder")} className="w-full resize-none bg-transparent text-sm outline-none placeholder:text-muted-foreground" />
        <div className="flex flex-wrap items-center gap-2">
          {EXAMPLES.map((k) => <button type="button" key={k} onClick={() => setQ(t(k))} className="rounded-sm border border-border px-2 py-1 text-left text-[11px] text-muted-foreground hover:text-foreground">{t(k)}</button>)}
          <Button type="submit" size="sm" className="ml-auto" disabled={m.isPending}>{m.isPending ? t("research.working") : t("research.ask")}</Button>
        </div>
      </form>
      {m.isError && (
        <div className="rounded-lg border border-negative/40 bg-negative/10 p-4 text-sm">
          <b>{t("common.error")}:</b> {(m.error as Error).message}
          <div className="mt-1 text-xs text-muted-foreground">{t("research.keyHint")}</div>
        </div>
      )}
      {m.data && (
        <>
          {m.data.unverified_numbers.length > 0 && (
            // Local engine: figures the answer states that no tool result carries — shown apart from the prose, never dropped.
            <div role="alert" className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">{t("research.unverified", { list: m.data.unverified_numbers.join(", ") })}</div>
          )}
          <Section title={t("research.answer")} hint={m.data.model}>
            <div className="whitespace-pre-wrap px-4 py-3 text-sm leading-relaxed">{m.data.answer}</div>
          </Section>
          <Section title={t("research.tools")} hint={`${m.data.tool_calls.length}`}>
            <ul className="divide-y divide-border/60 text-xs">
              {m.data.tool_calls.map((tc, i) => (
                <li key={i} className="px-4 py-2">
                  <span className="font-mono font-semibold">{tc.name}</span> <span className="text-muted-foreground">{JSON.stringify(tc.input)}</span>
                  <pre className="mt-1 truncate text-muted-foreground/70">{tc.output_preview}</pre>
                </li>
              ))}
            </ul>
          </Section>
        </>
      )}
    </div>
  )
}
