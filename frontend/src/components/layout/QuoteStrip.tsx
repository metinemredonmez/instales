import type { Quote } from "@/lib/api"
import { fmtDateTime } from "@/lib/format"
import { useI18n } from "@/lib/i18n"
import { useCountUp, useFlash } from "@/lib/motion"
import { fmtQuoteChange, fmtQuotePrice, quoteTone, useQuotes } from "@/lib/quotes"
import { cn } from "@/lib/utils"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"

/**
 * USD/TRY · EUR/TRY · BIST 100 · S&P 500 from /quotes (60 s). Renders nothing until the first answer arrives and
 * only the quotes the API actually returned — a source that failed leaves a gap, never a placeholder number.
 * Row mode is a flex row that wraps into 2×2 at lg–xl (the header is too narrow for one line there) and runs as a
 * single line from 2xl up; `stack` lays the rows vertically (used inside the account menu on small screens).
 */
export function QuoteStrip({ className, stack = false }: { className?: string; stack?: boolean }) {
  const { t } = useI18n()
  const q = useQuotes()
  const quotes = q.data?.quotes ?? []
  if (quotes.length === 0) return null
  return (
    <TooltipProvider>
      <div role="list" aria-label={t("quotes.label")} title={q.data ? t("quotes.asOf", { at: fmtDateTime(q.data.as_of) }) : undefined} className={cn("flex", stack ? "flex-col gap-1" : "w-[300px] shrink-0 flex-wrap items-center 2xl:w-auto 2xl:flex-nowrap 2xl:gap-0.5", className)}>
        {quotes.map((x) => <QuoteItem key={x.key} quote={x} stack={stack} />)}
      </div>
    </TooltipProvider>
  )
}

function QuoteItem({ quote, stack }: { quote: Quote; stack: boolean }) {
  const { t } = useI18n()
  const tone = quoteTone(quote.change_pct)
  const value = useCountUp(quote.price)
  const flash = useFlash(quote.price, tone === "flat" ? "info" : tone)
  return (
    <div role="listitem" className={cn("flex items-baseline gap-1 rounded-sm px-1 py-0.5 text-xs", stack ? "justify-between" : "basis-1/2 2xl:basis-auto", flash)}>
      <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">{quote.label}</span>
      <span className={cn("flex items-baseline gap-1.5", stack && "ml-auto")}>
        <span className="num font-medium text-foreground">{fmtQuotePrice(value ?? quote.price, quote.decimals)}</span>
        {quote.change_pct !== null && (
          <span className={cn("num text-[11px]", tone === "pos" ? "text-positive" : tone === "neg" ? "text-negative" : "text-muted-foreground")}>{fmtQuoteChange(quote.change_pct)}</span>
        )}
        {quote.stale && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span role="img" tabIndex={0} aria-label={t("quotes.stale", { at: fmtDateTime(quote.updated_at) })} className="inline-block size-1.5 self-center rounded-full bg-muted-foreground/60" />
            </TooltipTrigger>
            <TooltipContent side="bottom" sideOffset={4}>{t("quotes.stale", { at: fmtDateTime(quote.updated_at) })}</TooltipContent>
          </Tooltip>
        )}
      </span>
    </div>
  )
}
