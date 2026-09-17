import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { useState, type FormEvent } from "react"
import { Trash2 } from "lucide-react"
import { api, type AlertRule, type Market, type PriceRuleType, type RuleParams, type RuleType } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { useI18n, type T } from "@/lib/i18n"
import { fmtDateTime, fmtNum } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const ruleLabel = (t: T): Record<string, string> => ({
  NEW_FUND_POSITION: t("alerts.rule.newFundPosition"),
  FUND_EXIT: t("alerts.rule.fundExit"),
  KAP_TRANSACTION: t("alerts.rule.kapTransaction"),
  SCORE_ABOVE: t("alerts.rule.scoreAbove"),
  SIGNAL: t("common.signal"),
  FUND_ACTIVITY: t("alerts.rule.fundActivity"),
  INSIDER_BUY_CLUSTER: t("alerts.rule.insiderBuyCluster"),
  PRICE_ABOVE: t("alerts.rule.priceAbove"),
  PRICE_BELOW: t("alerts.rule.priceBelow"),
})

/** Price thresholds are in the market's listing currency: closes come from market_prices, TRY on BIST and USD on US listings. */
const PRICE_CURRENCY: Record<Market, string> = { TR: "TRY", US: "USD" }
const PRICE_SYMBOL: Record<Market, string> = { TR: "₺", US: "$" }
const isPriceRule = (type: string): type is PriceRuleType => type === "PRICE_ABOVE" || type === "PRICE_BELOW"
/** "120 ₺", "123,4 ₺" — the threshold as the notification text phrases it: amount then symbol, only the decimals the user typed (at most two). */
const fmtThreshold = (v: number, market: Market) => `${fmtNum(v, Number.isInteger(v) ? 0 : Number.isInteger(v * 10) ? 1 : 2)} ${PRICE_SYMBOL[market]}`
/** The subject field's shape rule, shared with the mutation: a three-letter code on BIST is a fund (TMV), anything else a stock. */
const isFundCode = (market: Market, ref: string) => market === "TR" && ref.length === 3

/**
 * Price the form will send, or the reason it must not: the field is text so a half-typed "12," never snaps to 0, and
 * the rule needs a finite price above zero. A fund code cannot carry a price rule (closes exist for stocks only).
 */
export function priceRuleError(t: T, market: Market, ref: string, price: string): string | null {
  if (isFundCode(market, ref)) return t("alerts.price.stockOnly")
  const n = Number(price.trim().replace(",", "."))
  return price.trim() === "" || !Number.isFinite(n) || n <= 0 ? t("alerts.price.invalid") : null
}

/**
 * "ASELS > 120 ₺" — the rule's own threshold in its subject's currency (the list is owner-wide, so a BIST rule keeps
 * ₺ on the US view), the way the notification will phrase it; a rule without a usable price shows a dash.
 */
export function ruleSuffix(r: AlertRule): string {
  if (r.rule_type === "SCORE_ABOVE") return ` ≥ ${String(r.params.threshold ?? 80)}`
  if (isPriceRule(r.rule_type)) {
    const p = Number(r.params.price)
    return ` ${r.rule_type === "PRICE_ABOVE" ? ">" : "<"} ${Number.isFinite(p) && r.params.price !== null && r.params.price !== undefined ? fmtThreshold(p, r.market) : "—"}`
  }
  return ""
}

export function AlertsPage() {
  const { market } = useMarket()
  const { t } = useI18n()
  const RULE_LABEL = ruleLabel(t)
  const qc = useQueryClient()
  const rules = useQuery({ queryKey: ["rules"], queryFn: api.rules })
  const notes = useQuery({ queryKey: ["notifications"], queryFn: api.notifications, refetchInterval: 30_000 })
  const [ref, setRef] = useState("")
  const [type, setType] = useState<RuleType | string>("NEW_FUND_POSITION")
  const [threshold, setThreshold] = useState(80)
  const [price, setPrice] = useState("")
  const [err, setErr] = useState<string | null>(null)
  const invalidate = () => { qc.invalidateQueries({ queryKey: ["rules"] }); qc.invalidateQueries({ queryKey: ["notifications"] }) }
  const add = useMutation({
    mutationFn: () => {
      const r = ref.trim().toUpperCase()
      const subject = isFundCode(market, r) ? { fund_code: r } : { symbol: r }
      const params: RuleParams = type === "SCORE_ABOVE" ? { threshold } : isPriceRule(type) ? { price: Number(price.trim().replace(",", ".")) } : {}
      return api.addRule({ ...subject, market, rule_type: type, params })
    },
    onSuccess: () => { invalidate(); setRef(""); setPrice(""); setErr(null) },
    onError: (e) => setErr((e as Error).message),
  })
  const remove = useMutation({ mutationFn: api.removeRule, onSuccess: invalidate })
  const evaluate = useMutation({ mutationFn: api.evaluateAlerts, onSuccess: invalidate })
  const markAll = useMutation({ mutationFn: () => api.markRead(), onSuccess: invalidate })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const r = ref.trim().toUpperCase()
    if (!r) return
    // Price rules are checked here before anything is sent: the API would refuse them too, but the message should name the field.
    const problem = isPriceRule(type) ? priceRuleError(t, market, r, price) : null
    setErr(problem)
    if (!problem) add.mutate()
  }
  const active = rules.data?.rules.filter((r) => r.is_active) ?? []
  const unread = notes.data?.filter((n) => !n.read_at).length ?? 0
  // Every rule type the API lists is offered on both markets (the insider cluster reads Form 4 on US issuers, KAP
  // filings on BIST). The price rules are for stocks only: while the typed subject is a fund code they stay listed but
  // cannot be picked (disabled, not removed — a BIST symbol passes through three letters on its way to five, and the
  // field must not jump), and a choice made before the code was typed is refused at submit with the same reason.
  const ruleTypes = rules.data?.rule_types ?? Object.keys(RULE_LABEL)
  const fund = isFundCode(market, ref.trim())

  return (
    <div className="space-y-5">
      <div><h1 className="text-2xl font-semibold tracking-tight">{t("alerts.title")}</h1><p className="text-sm text-muted-foreground">{t("alerts.sub")}</p></div>
      <div className="grid gap-5 lg:grid-cols-[380px_1fr]">
        <div className="space-y-5">
          <Section title={t("alerts.newRule")}>
            <form onSubmit={submit} className="space-y-3 p-4 text-sm">
              <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("alerts.subject")}</div><input value={ref} onChange={(e) => setRef(e.target.value)} className="h-9 w-full rounded-md border border-input bg-background px-3 outline-none focus:ring-2 focus:ring-ring/40" /></label>
              <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("alerts.rule")}</div>
                <select value={type} onChange={(e) => { setType(e.target.value); setErr(null) }} className="h-9 w-full rounded-md border border-input bg-background px-2 outline-none">
                  {ruleTypes.map((k) => <option key={k} value={k} disabled={fund && isPriceRule(k)}>{RULE_LABEL[k] ?? k}</option>)}
                </select>
              </label>
              {type === "SCORE_ABOVE" && <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("alerts.threshold")}</div><input type="number" value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} className="num h-9 w-full rounded-md border border-input bg-background px-3 outline-none" /></label>}
              {isPriceRule(type) && (
                <label className="block">
                  <div className="mb-1 text-xs text-muted-foreground">{t("alerts.price", { cur: PRICE_CURRENCY[market] })}</div>
                  <input type="number" inputMode="decimal" step="any" value={price} onChange={(e) => { setPrice(e.target.value); setErr(null) }} placeholder={market === "TR" ? "120" : "150"} className="num h-9 w-full rounded-md border border-input bg-background px-3 outline-none focus:ring-2 focus:ring-ring/40" />
                  <div className="mt-1 text-[11px] text-muted-foreground">{t("alerts.price.hint")}</div>
                </label>
              )}
              {err && <div role="alert" className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-xs">{err}</div>}
              <Button type="submit" size="sm" className="w-full" disabled={add.isPending}>{t("alerts.addRule")}</Button>
            </form>
          </Section>
          <Section title={t("alerts.activeRules")} hint={`${active.length}`}>
            <ul className="divide-y divide-border/60 text-sm">
              {active.map((r) => (
                <li key={r.id} className="flex items-center gap-3 px-4 py-2.5">
                  <Link to={r.symbol ? `/stocks/${r.symbol}` : `/funds/${r.fund_code}`} className="font-semibold hover:underline">{r.symbol ?? r.fund_code}</Link>
                  <span className="text-muted-foreground">{RULE_LABEL[r.rule_type] ?? r.rule_type}{ruleSuffix(r)}</span>
                  <button onClick={() => remove.mutate(r.id)} className="ml-auto text-muted-foreground hover:text-negative" aria-label={t("common.delete")}><Trash2 className="size-4" /></button>
                </li>
              ))}
              {active.length === 0 && <li className="px-4 py-6 text-muted-foreground">{t("alerts.noRules")}</li>}
            </ul>
          </Section>
        </div>
        <Section title={t("alerts.notifications")} hint={unread ? t("alerts.unread", { n: unread }) : undefined} right={
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={() => evaluate.mutate()} disabled={evaluate.isPending}>{t("alerts.evaluate")}</Button>
            <Button variant="ghost" size="sm" onClick={() => markAll.mutate()} disabled={!unread}>{t("alerts.markAll")}</Button>
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
            {notes.data?.length === 0 && <li className="px-4 py-8 text-center text-sm text-muted-foreground">{t("alerts.empty")}</li>}
          </ul>
        </Section>
      </div>
    </div>
  )
}
