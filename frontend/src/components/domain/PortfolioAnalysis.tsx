import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { api, type Portfolio } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { useMarket } from "@/lib/market"
import { useAuth } from "@/lib/auth"
import { fmtDate, fmtNum } from "@/lib/format"
import { Section, Stat } from "@/components/layout/Section"
import { ConfidenceBadge } from "@/components/domain/badges"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"

export function PortfolioAnalysis({ portfolio }: { portfolio: Portfolio }) {
  const { t } = useI18n()
  const { setMarket } = useMarket()
  const { user } = useAuth()
  const q = useQuery({
    queryKey: ["portfolio-analysis", user?.id, portfolio.id],
    queryFn: () => api.portfolioAnalysis(portfolio.id),
    enabled: !!user,
    staleTime: 60_000, refetchInterval: 120_000,
  })
  const pct = (n: number | null) => n === null ? "—" : `${fmtNum(n, 2)}%`
  const activateMarket = () => setMarket(portfolio.market)
  if (q.isPending) return <Section title={t("pa.title")}><Skeleton className="m-4 h-32" /></Section>
  if (q.isError) return (
    <Section title={t("pa.title")}>
      <div role="alert" className="flex flex-wrap items-center gap-3 p-4 text-sm">
        <span>{t("pa.error")}</span>
        <Button variant="outline" size="sm" onClick={() => q.refetch()}>{t("pa.retry")}</Button>
      </div>
    </Section>
  )
  const d = q.data
  const c = d.concentration
  const r = d.risk
  const funds = d.common_funds.rows
  return (
    <Section title={t("pa.title")} hint={fmtDate(d.as_of)}>
      <div className="space-y-5 p-4">
        <div>
          <p className="text-sm text-muted-foreground">{t("pa.coverage", { priced: c.priced_positions, total: c.total_positions })}</p>
          {c.missing_symbols.length > 0 && <p className="mt-1 text-sm text-warning">{t("pa.missing", { symbols: c.missing_symbols.join(", ") })}</p>}
          {c.stale_symbols.length > 0 && <p className="mt-1 text-sm text-warning">{t("pa.stale", { symbols: c.stale_symbols.join(", ") })}</p>}
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <Stat label={t("pa.largest")} value={pct(c.largest_weight_pct)} sub={c.allocations[0]?.symbol} />
          <Stat label={t("pa.top3")} value={pct(c.top3_weight_pct)} />
          <Stat label={t("pa.hhi")} value={c.hhi === null ? "—" : fmtNum(c.hhi, 0)} sub={t("pa.hhiHint")} />
        </div>
        {c.allocations.length > 0 && (
          <details className="rounded-md border border-border p-3">
            <summary className="cursor-pointer text-sm font-medium">{t("pa.allocation")}</summary>
            <ul className="mt-3 max-h-64 space-y-3 overflow-auto">
              {c.allocations.map((row) => (
                <li key={row.symbol}>
                  <div className="flex justify-between text-xs">
                    <Link to={`/stocks/${row.symbol}`} onClick={activateMarket} className="font-medium hover:underline">{row.symbol}</Link>
                    <span className="num">{pct(row.weight_pct)}</span>
                  </div>
                  <div className="mt-1 h-1.5 rounded bg-muted" aria-hidden="true"><div className="h-full rounded bg-primary" style={{ width: `${row.weight_pct}%` }} /></div>
                </li>
              ))}
            </ul>
          </details>
        )}
        <div className="space-y-3 border-t border-border pt-4">
          <h3 className="text-sm font-semibold">{t("pa.risk")}</h3>
          <p className="text-xs text-muted-foreground">{t("pa.basketHint")}</p>
          <p className="text-xs text-muted-foreground">{t("pa.priceHint")}</p>
          {r.start && r.end && <p className="text-xs text-muted-foreground">{fmtDate(r.start)} → {fmtDate(r.end)} · {t("pa.observations", { n: r.observations })}</p>}
          {r.status !== "ready" && <p role="status" className="rounded-md bg-muted p-3 text-sm">{t(`pa.status.${r.status}`, { n: r.min_returns, missing: r.missing_dates })}</p>}
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat label={t("pa.priceChange")} value={pct(r.price_change_pct)} />
            <Stat label={t("pa.volatility")} value={pct(r.annualized_volatility_pct)} />
            <Stat label={t("pa.drawdown")} value={pct(r.max_drawdown_pct)} />
          </div>
        </div>
        <div className="space-y-3 border-t border-border pt-4">
          <h3 className="text-sm font-semibold">{t("pa.funds")}</h3>
          <p className="text-xs text-muted-foreground">{t("pa.fundsHint")}</p>
          {funds.length === 0 ? <p className="text-sm text-muted-foreground">{t("pa.noFunds")}</p> : (
            <ul className="divide-y divide-border">
              {funds.map((fund) => (
                <li key={fund.code} className="space-y-1 py-2 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link to={`/funds/${fund.code}`} onClick={activateMarket} className="font-medium hover:underline">{fund.code} · {fund.name}</Link>
                    <ConfidenceBadge value={fund.confidence} />
                  </div>
                  <p>{fund.symbols.join(" · ")}</p>
                  <p className="text-xs text-muted-foreground">{fmtDate(fund.as_of)} · {t("pa.disclosure", { id: fund.disclosure_id })}</p>
                </li>
              ))}
            </ul>
          )}
          {d.common_funds.total > funds.length && <p className="text-xs text-muted-foreground">{t("pa.fundsLimit", { total: d.common_funds.total, n: funds.length })}</p>}
          {funds.length >= 2 && <Link to={`/compare?codes=${encodeURIComponent(funds.slice(0, 6).map((f) => f.code).join(","))}`} onClick={activateMarket} className="inline-block text-sm text-primary underline">{t("pa.compare")}</Link>}
        </div>
        <details className="rounded-md border border-border p-3 text-xs">
          <summary className="cursor-pointer font-medium">{t("pa.sources")}</summary>
          <ul className="mt-2 space-y-1 text-muted-foreground">
            {d.price_sources.map((source) => <li key={source.position_id}>{source.symbol} · {source.providers.join(", ") || "—"} · {source.latest_date ? fmtDate(source.latest_date) : "—"} · {t("pa.observations", { n: source.observations })}</li>)}
          </ul>
        </details>
      </div>
    </Section>
  )
}
