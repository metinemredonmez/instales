import { useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ExternalLink } from "lucide-react"
import { api, type Filing, type FilingForm, type Filings as FilingsData, type Market } from "@/lib/api"
import { fmtDate, fmtPeriod } from "@/lib/format"
import { useI18n, type T } from "@/lib/i18n"
import { Section } from "@/components/layout/Section"
import { Segmented } from "@/components/ui/segmented"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

const FORMS: FilingForm[] = ["8-K", "10-K", "10-Q", "4"]
type Pick = FilingForm | "all"
/** A compact list: the latest ten, the EDGAR index is one click away for the rest. */
const LIMIT = 10
/** 8-K items with a title of their own; any other item shows its code alone. */
const ITEMS = ["1.01", "1.02", "2.01", "2.02", "2.03", "5.02", "5.03", "5.07", "7.01", "8.01", "9.01"] as const
type Item = (typeof ITEMS)[number]
const itemTitle = (t: T, code: string) => ((ITEMS as readonly string[]).includes(code) ? t(`filings.item.${code as Item}`) : null)

/**
 * The issuer's latest EDGAR filings — form, filing date, period, the 8-K item codes with their titles — each linking
 * to its index page (and to the main document when EDGAR names one), with a form filter. Hides itself when nothing
 * has ever loaded and altogether on BIST symbols (`supported: false`); once a list has loaded it stays on screen
 * while another filter loads or fails. An issuer the daily job has not read yet (`fetched_at` null) says so instead
 * of claiming nothing was filed — every listed US issuer files.
 */
export function Filings({ symbol, market }: { symbol: string; market: Market }) {
  const { t } = useI18n()
  const [pick, setPick] = useState<Pick>("all")
  const q = useQuery({ queryKey: ["filings", market, symbol, pick], queryFn: () => api.filings(market, symbol, pick === "all" ? null : pick, LIMIT), placeholderData: (prev) => prev })
  const last = useRef<FilingsData>(undefined)
  if (q.data) last.current = q.data
  const d = q.data ?? last.current

  if (!d) return q.isError ? null : <Skeleton className="h-40" />
  if (!d.supported) return null
  const options: { value: Pick; label: string }[] = [{ value: "all", label: t("common.all") }, ...FORMS.map((f) => ({ value: f, label: f }))]

  return (
    <Section title={t("filings.title")} hint={t("filings.hint")} right={<Segmented value={pick} options={options} onChange={setPick} />} className={cn(q.isPlaceholderData && "opacity-60 transition-opacity")}>
      {q.isError && <div className="px-4 py-2 text-xs text-negative">{t("filings.error")}</div>}
      {d.filings.length === 0 ? (
        <div className="px-4 py-6 text-sm text-muted-foreground">{d.fetched_at === null ? t("filings.notFetched") : t("filings.empty")}</div>
      ) : (
        <ul className="divide-y divide-border/60 text-sm">{d.filings.map((f) => <FilingRow key={f.accession} f={f} />)}</ul>
      )}
      <div className="border-t border-border/60 px-4 py-2 text-[11px] text-muted-foreground">{t("quotes.source", { src: t("filings.source") })}</div>
    </Section>
  )
}

function FilingRow({ f }: { f: Filing }) {
  const { t } = useI18n()
  const chip = "inline-flex min-w-12 shrink-0 justify-center rounded-sm border border-border bg-secondary px-1.5 py-0.5 font-mono text-[11px] font-semibold"
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2">
      {f.primary_url ? (
        <a href={f.primary_url} target="_blank" rel="noopener noreferrer" title={t("filings.document")} className={cn(chip, "hover:underline")}>{f.form}</a>
      ) : (
        <span className={chip}>{f.form}</span>
      )}
      <span className="num shrink-0 text-xs text-muted-foreground">{fmtDate(f.filed_at)}</span>
      {f.period && <span className="num shrink-0 text-xs text-muted-foreground">{t("filings.period", { p: fmtPeriod(f.period) })}</span>}
      {f.items.length > 0 && (
        <span className="flex flex-wrap gap-1">
          {f.items.map((code) => {
            const title = itemTitle(t, code)
            return (
              <span key={code} className="inline-flex items-center gap-1 rounded-sm border border-border px-1.5 py-0.5 text-[11px]">
                <span className="num text-muted-foreground">{code}</span>
                {title && <span>{title}</span>}
              </span>
            )
          })}
        </span>
      )}
      <a href={f.url} target="_blank" rel="noopener noreferrer" title={t("filings.edgar")} className="ml-auto inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        EDGAR <ExternalLink className="size-3" />
      </a>
    </li>
  )
}
