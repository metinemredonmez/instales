import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Trash2 } from "lucide-react"
import { api, type Market, type NewsRule } from "@/lib/api"
import { Section } from "@/components/layout/Section"
import { Button } from "@/components/ui/button"

const EMPTY: Omit<NewsRule, "id"> = { name: "", market_code: "TR", query: "", exclusion: "", only_sources: "", remove_sources: "", language: "", max_age_days: 3, symbols: [], newsapi_query: "", ai_summary: true, is_active: true }

/** Newsomatic-style import rules: keywords (comma = OR, + = AND, * = suffix wildcard), exclusions, source filters, symbol tags. */
export function NewsRulesAdmin() {
  const qc = useQueryClient()
  const rules = useQuery({ queryKey: ["admin", "news-rules"], queryFn: api.adminNewsRules })
  const [draft, setDraft] = useState<Omit<NewsRule, "id">>(EMPTY)
  const [symbolsText, setSymbolsText] = useState("")
  const inv = () => { qc.invalidateQueries({ queryKey: ["admin", "news-rules"] }); qc.invalidateQueries({ queryKey: ["news"] }) }
  const create = useMutation({ mutationFn: () => api.adminNewsRuleCreate({ ...draft, symbols: symbolsText.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean) }), onSuccess: () => { inv(); setDraft(EMPTY); setSymbolsText("") } })
  const toggle = useMutation({ mutationFn: (r: NewsRule) => api.adminNewsRuleUpdate(r.id, { ...r, is_active: !r.is_active }), onSuccess: inv })
  const remove = useMutation({ mutationFn: api.adminNewsRuleDelete, onSuccess: inv })
  const seed = useMutation({ mutationFn: api.adminNewsSeed, onSuccess: inv })
  const reapply = useMutation({ mutationFn: api.adminNewsReapply, onSuccess: inv })
  const enrich = useMutation({ mutationFn: (m: Market) => api.adminNewsEnrich(m), onSuccess: inv })
  const field = (label: string, key: keyof typeof draft, placeholder = "") => (
    <label className="block text-xs"><div className="mb-1 text-muted-foreground">{label}</div><input value={String(draft[key] ?? "")} placeholder={placeholder} onChange={(e) => setDraft({ ...draft, [key]: key === "max_age_days" ? Number(e.target.value) : e.target.value })} className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm outline-none" /></label>
  )

  return (
    <Section title="Haber kuralları" hint="virgül = VEYA · + = VE · * = son ek" right={
      <div className="flex gap-2">
        <Button variant="ghost" size="sm" onClick={() => seed.mutate()}>Varsayılanları ekle</Button>
        <Button variant="ghost" size="sm" onClick={() => reapply.mutate()}>Kuralları yeniden uygula</Button>
        <Button variant="ghost" size="sm" onClick={() => enrich.mutate("TR")} disabled={enrich.isPending}>AI etiketle (TR){enrich.data ? ` · ${enrich.data.tagged}` : ""}</Button>
      </div>
    }>
      <div className="grid gap-3 p-4 md:grid-cols-4">
        {field("Ad", "name", "Bankacılık")}
        <label className="block text-xs"><div className="mb-1 text-muted-foreground">Piyasa</div><select value={draft.market_code} onChange={(e) => setDraft({ ...draft, market_code: e.target.value as Market })} className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm"><option>TR</option><option>US</option></select></label>
        {field("Anahtar kelimeler", "query", "banka*, faiz+artış")}
        {field("Hariç", "exclusion", "magazin, çin")}
        {field("Sadece kaynaklar", "only_sources", "AA Ekonomi, Dünya")}
        {field("Kaynak hariç", "remove_sources")}
        <label className="block text-xs"><div className="mb-1 text-muted-foreground">Etiketlenecek hisseler</div><input value={symbolsText} placeholder="GARAN, AKBNK" onChange={(e) => setSymbolsText(e.target.value)} className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm outline-none" /></label>
        {field("NewsAPI sorgusu (opsiyonel)", "newsapi_query", "Türkiye bankacılık")}
        <div className="flex items-end"><Button size="sm" onClick={() => create.mutate()} disabled={!draft.name || create.isPending}>Kural ekle</Button></div>
      </div>
      <table className="w-full text-sm">
        <thead className="text-[11px] uppercase tracking-wider text-muted-foreground"><tr className="border-b border-border/60"><th className="px-4 py-2 text-left font-medium">Ad</th><th className="px-2 py-2 text-left font-medium">Piyasa</th><th className="px-2 py-2 text-left font-medium">Kelimeler</th><th className="px-2 py-2 text-left font-medium">Hariç</th><th className="px-2 py-2 text-left font-medium">Hisseler</th><th className="px-2 py-2 text-left font-medium">Aktif</th><th className="w-10" /></tr></thead>
        <tbody>
          {rules.data?.map((r) => (
            <tr key={r.id} className="border-b border-border/40 last:border-0">
              <td className="px-4 py-2 font-medium">{r.name}</td><td className="px-2 py-2">{r.market_code}</td>
              <td className="max-w-[260px] truncate px-2 py-2 text-xs text-muted-foreground" title={r.query}>{r.query}</td>
              <td className="max-w-[160px] truncate px-2 py-2 text-xs text-muted-foreground" title={r.exclusion}>{r.exclusion}</td>
              <td className="px-2 py-2 text-xs">{r.symbols.join(", ")}</td>
              <td className="px-2 py-2"><input type="checkbox" checked={r.is_active} onChange={() => toggle.mutate(r)} /></td>
              <td className="px-2 py-2 text-right"><button onClick={() => remove.mutate(r.id)} className="text-muted-foreground hover:text-negative" aria-label="Sil"><Trash2 className="size-4" /></button></td>
            </tr>
          ))}
          {rules.data?.length === 0 && <tr><td colSpan={7} className="px-4 py-6 text-center text-sm text-muted-foreground">Kural yok — "Varsayılanları ekle" ile başla.</td></tr>}
        </tbody>
      </table>
    </Section>
  )
}
