import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { Section } from "@/components/layout/Section"
import { Button } from "@/components/ui/button"

/** Entities the resolver auto-created from unknown codes; a human confirms the name before they count as verified. */
export function AdminReviewPage() {
  const { t } = useI18n()
  const qc = useQueryClient()
  const review = useQuery({ queryKey: ["admin", "review"], queryFn: api.adminReview })
  const verify = useMutation({ mutationFn: api.adminVerify, onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "review"] }) })
  const r = review.data
  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("admin.review.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("admin.review.sub")}</p>
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <ReviewList title={t("admin.review.stocks")} rows={r?.instruments.map((i) => ({ id: i.id, label: `${i.market} · ${i.symbol}`, name: i.name })) ?? []} onVerify={(id, name) => verify.mutate({ kind: "instrument", id, name })} />
        <ReviewList title={t("admin.review.funds")} rows={r?.funds.map((f) => ({ id: f.id, label: `${f.code} · ${f.institution}`, name: f.name })) ?? []} onVerify={(id, name) => verify.mutate({ kind: "fund", id, name })} />
        <ReviewList title={t("admin.review.institutions")} rows={r?.institutions.map((i) => ({ id: i.id, label: `${i.market} · ${i.code}`, name: i.name })) ?? []} onVerify={(id, name) => verify.mutate({ kind: "institution", id, name })} />
        <Section title={t("admin.review.parseErrors")} hint={`${r?.failed_disclosures.length ?? 0}`}>
          <ul className="divide-y divide-border/60 text-xs">
            {r?.failed_disclosures.map((d) => <li key={d.id} className="px-4 py-2"><span className="font-mono">{d.source} #{d.source_id}</span> <span className="text-muted-foreground">{d.kind}</span><div className="text-negative">{d.error}</div></li>)}
            {r?.failed_disclosures.length === 0 && <li className="px-4 py-6 text-muted-foreground">{t("common.none")}</li>}
          </ul>
        </Section>
      </div>
    </>
  )
}

function ReviewList({ title, rows, onVerify }: { title: string; rows: { id: number; label: string; name: string }[]; onVerify: (id: number, name: string) => void }) {
  const { t } = useI18n()
  return (
    <Section title={title} hint={`${rows.length}`}>
      <ul className="divide-y divide-border/60 text-sm">
        {rows.slice(0, 50).map((r) => (
          <li key={r.id} className="flex items-center gap-2 px-4 py-2">
            <span className="w-40 shrink-0 truncate font-mono text-xs">{r.label}</span>
            <input defaultValue={r.name} id={`n-${title}-${r.id}`} className="h-7 flex-1 rounded border border-input bg-background px-2 text-xs" />
            <Button size="sm" variant="outline" onClick={() => onVerify(r.id, (document.getElementById(`n-${title}-${r.id}`) as HTMLInputElement).value)}>{t("admin.review.verify")}</Button>
          </li>
        ))}
        {rows.length === 0 && <li className="px-4 py-6 text-muted-foreground">{t("common.none")}</li>}
        {rows.length > 50 && <li className="px-4 py-2 text-xs text-muted-foreground">{t("common.more", { n: rows.length - 50 })}</li>}
      </ul>
    </Section>
  )
}
