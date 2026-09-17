import { useEffect, useId, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { Dialog } from "radix-ui"
import { Briefcase, Pencil, Plus, Trash2, X } from "lucide-react"
import { api, isPlanLimit, type Market, type MoveParty, type MoveRow, type Portfolio, type PortfolioDetail, type PortfolioPosition, type PortfolioTransaction } from "@/lib/api"
import { hasFeature, useAuth } from "@/lib/auth"
import { useMarket } from "@/lib/market"
import { useI18n, type T } from "@/lib/i18n"
import { fmtCompact, fmtDate, fmtLots, fmtMoney, fmtNum, fmtPrice, fmtQty } from "@/lib/format"
import { Section, Stat } from "@/components/layout/Section"
import { ACT, ConfidenceBadge, CrowdingPill, Flow, ScorePill } from "@/components/domain/badges"
import { PlanGate, limitMessage } from "@/components/domain/PlanGate"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

/** The plan matrix keys that gate this page: the on/off flag and the portfolio count (0 on FREE). */
const FEATURES = ["portfolio", "portfolios"]
/** A portfolio is priced in its market's listing currency; fmtMoney keys the symbol by market. */
const flowMarket = (p: Portfolio): Market => (p.currency === "USD" ? "US" : p.currency === "TRY" ? "TR" : p.market)
const input = "h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring/40"
/** Text fields for amounts so a half-typed "12," never snaps to 0; a decimal comma is accepted. */
const num = (s: string) => Number(s.trim().replace(",", "."))
const today = () => new Date().toISOString().slice(0, 10)

/** What the add-position form will send, or the reason it must not: a symbol, a positive quantity, and a positive cost when one is typed. */
export function positionFormError(t: T, f: { symbol: string; quantity: string; avg_cost: string }): string | null {
  if (!f.symbol.trim()) return t("pf.symbolInvalid")
  const q = num(f.quantity)
  if (f.quantity.trim() === "" || !Number.isFinite(q) || q <= 0) return t("pf.qtyInvalid")
  if (f.avg_cost.trim() !== "") { const c = num(f.avg_cost); if (!Number.isFinite(c) || c <= 0) return t("pf.priceInvalid") }
  return null
}
/** Same for a transaction: symbol, quantity > 0, price > 0, a trade date. */
export function transactionFormError(t: T, f: { symbol: string; quantity: string; price: string; traded_at: string }): string | null {
  if (!f.symbol.trim()) return t("pf.symbolInvalid")
  const q = num(f.quantity)
  if (f.quantity.trim() === "" || !Number.isFinite(q) || q <= 0) return t("pf.qtyInvalid")
  const p = num(f.price)
  if (f.price.trim() === "" || !Number.isFinite(p) || p <= 0) return t("pf.priceInvalid")
  if (!f.traded_at) return t("pf.dateInvalid")
  return null
}
/** A mutation's failure as the dialog prints it: the plan message for a 402, the API text otherwise. */
const errorText = (t: T, e: unknown) => (isPlanLimit(e) ? limitMessage(t, e) : (e as Error).message)

/**
 * /portfolio — the user's portfolios (picker + create/rename/delete), the picked one's positions with the institutional
 * context of the stock page (scores, 30-day fund head-counts, US insiders), totals, and the last 30 days of
 * institutional moves on the held symbols. Plan-gated: FREE has no portfolio, so the body is a PlanGate — the account's
 * matrix locks it before any request, a 402 on the list locks it after. Positions come from transactions (FIFO cost)
 * when the portfolio has any, else they are entered directly; both dialogs live here.
 */
export function PortfolioPage() {
  const { t } = useI18n()
  const { user } = useAuth()
  const { market } = useMarket()
  const qc = useQueryClient()
  const gated = hasFeature(user, FEATURES)
  const list = useQuery({ queryKey: ["portfolios"], queryFn: api.portfolios, enabled: gated, retry: (n, e) => !isPlanLimit(e) && n < 2 })
  const [picked, setPicked] = useState<number | null>(null)
  // The pick, or the first portfolio of the header's market, or the first one there is.
  const current = useMemo(() => {
    const ps = list.data ?? []
    return ps.find((p) => p.id === picked) ?? ps.find((p) => p.market === market) ?? ps[0] ?? null
  }, [list.data, picked, market])
  const detail = useQuery({ queryKey: ["portfolio", current?.id], queryFn: () => api.portfolio(current!.id), enabled: current !== null, refetchInterval: 120_000, retry: (n, e) => !isPlanLimit(e) && n < 2 })
  const [dialog, setDialog] = useState<"create" | "rename" | "position" | "transaction" | null>(null)
  // The last failed removal, printed under the section it belongs to (a 400 on a buy whose sale would then oversell is a normal answer, not a crash).
  const [removeErr, setRemoveErr] = useState<string | null>(null)
  const invalidate = () => { qc.invalidateQueries({ queryKey: ["portfolios"] }); qc.invalidateQueries({ queryKey: ["portfolio"] }) }
  const failed = (e: unknown) => setRemoveErr(errorText(t, e))
  const del = useMutation({ mutationFn: api.deletePortfolio, onSuccess: () => { setPicked(null); setRemoveErr(null); invalidate() }, onError: failed })
  const removePos = useMutation({ mutationFn: ({ id, pos }: { id: number; pos: number }) => api.deletePosition(id, pos), onSuccess: () => { setRemoveErr(null); invalidate() }, onError: failed })
  const removeTx = useMutation({ mutationFn: ({ id, tx }: { id: number; tx: number }) => api.deleteTransaction(id, tx), onSuccess: () => { setRemoveErr(null); invalidate() }, onError: failed })
  const close = () => setDialog(null)

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-2xl font-semibold tracking-tight">{t("pf.title")}</h1><p className="text-sm text-muted-foreground">{t("pf.sub")}</p></div>
        {gated && (list.data?.length ?? 0) > 0 && current && (
          <div className="flex flex-wrap items-center gap-2">
            <select aria-label={t("pf.title")} value={current.id} onChange={(e) => setPicked(Number(e.target.value))} className="h-9 rounded-md border border-input bg-card px-2 text-sm outline-none">
              {list.data!.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.market}</option>)}
            </select>
            <Button type="button" variant="outline" size="sm" onClick={() => setDialog("rename")} aria-label={t("pf.rename")} title={t("pf.rename")}><Pencil /></Button>
            <Button type="button" variant="outline" size="sm" onClick={() => { if (confirm(t("pf.deleteConfirm", { name: current.name }))) del.mutate(current.id) }} disabled={del.isPending} aria-label={t("pf.delete")} title={t("pf.delete")}><Trash2 /></Button>
            <Button type="button" size="sm" onClick={() => setDialog("create")}><Plus /> {t("pf.new")}</Button>
          </div>
        )}
      </div>

      <PlanGate feature={FEATURES} error={list.error ?? detail.error} title={t("pf.locked")} body={t("pf.locked.hint")}>
        {removeErr && <div role="alert" className="mb-3 rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-sm">{removeErr}</div>}
        {list.isPending ? (
          // No data and no error yet — the first fetch, or a paused one (offline): never the empty state.
          <Skeleton className="h-64" />
        ) : list.isError ? (
          <div className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-sm">{t("pf.error")}</div>
        ) : !current ? (
          <div className="rise mx-auto max-w-md rounded-lg border border-border bg-card px-6 py-8 text-center">
            <div className="mx-auto grid size-10 place-items-center rounded-full bg-muted text-muted-foreground"><Briefcase className="size-5" /></div>
            <div className="mt-3 text-sm font-semibold">{t("pf.none")}</div>
            <p className="mt-1 text-sm text-muted-foreground">{t("pf.none.hint")}</p>
            <Button type="button" size="sm" className="mt-4" onClick={() => setDialog("create")}><Plus /> {t("pf.new")}</Button>
          </div>
        ) : !detail.data ? (
          detail.isError ? <div className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-sm">{t("pf.error")}</div> : <Skeleton className="h-64" />
        ) : (
          <PortfolioBody d={detail.data} onAddPosition={() => setDialog("position")} onAddTransaction={() => setDialog("transaction")} onRemovePosition={(pos) => removePos.mutate({ id: current.id, pos })} onRemoveTransaction={(id) => removeTx.mutate({ id: current.id, tx: id })} />
        )}
      </PlanGate>

      <NameDialog open={dialog === "create" || dialog === "rename"} mode={dialog === "rename" ? "rename" : "create"} portfolio={dialog === "rename" ? current : null} market={market} onClose={close} onSaved={(p) => { setPicked(p.id); invalidate(); close() }} />
      {current && <PositionDialog open={dialog === "position"} portfolio={current} onClose={close} onSaved={() => { invalidate(); close() }} />}
      {current && <TransactionDialog open={dialog === "transaction"} portfolio={current} onClose={close} onSaved={() => { invalidate(); close() }} />}
    </div>
  )
}

function PortfolioBody({ d, onAddPosition, onAddTransaction, onRemovePosition, onRemoveTransaction }: { d: PortfolioDetail; onAddPosition: () => void; onAddTransaction: () => void; onRemovePosition: (positionId: number) => void; onRemoveTransaction: (id: number) => void }) {
  const { t } = useI18n()
  const p = d.portfolio
  const fm = flowMarket(p)
  const tone = (v: number | null | undefined) => (v === null || v === undefined || v === 0 ? undefined : v > 0 ? "pos" : "neg")
  const derived = (d.transactions?.length ?? 0) > 0
  const moves = d.moves?.rows ?? []
  const movesHint = d.moves ? `${t("pf.moves.hint")} · ${d.moves.window_days} ${t("common.days")} · ${fmtDate(d.moves.window_start)} → ${fmtDate(d.as_of)}` : t("pf.moves.hint")
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 rise-stagger">
        <Stat label={t("pf.marketValue")} value={fmtCompact(d.totals.market_value, p.currency)} sub={d.totals.unpriced ? t("pf.unpriced", { n: d.totals.unpriced }) : undefined} />
        <Stat label={t("pf.costValue")} value={fmtCompact(d.totals.cost_value, p.currency)} />
        <Stat label={t("pf.pnl")} value={fmtMoney(d.totals.pnl_value, fm)} tone={tone(d.totals.pnl_value)} />
        <Stat label={t("pf.pnlPct")} value={d.totals.pnl_pct === null ? "—" : `${fmtNum(d.totals.pnl_pct, 1, true)}%`} tone={tone(d.totals.pnl_pct)} />
      </div>

      <Section
        title={t("pf.positions")}
        hint={`${d.positions.length}${derived ? ` · ${t("pf.derived")}` : ""}`}
        right={
          <div className="flex gap-2">
            <Button type="button" variant="outline" size="sm" onClick={onAddTransaction}>{t("pf.addTx")}</Button>
            <Button type="button" size="sm" onClick={onAddPosition}><Plus /> {t("pf.addPosition")}</Button>
          </div>
        }
      >
        {d.positions.length === 0 ? (
          <div className="px-4 py-10 text-center text-sm">
            <div className="font-medium">{t("pf.empty")}</div>
            <div className="mt-1 text-muted-foreground">{t("pf.empty.hint")}</div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <PositionsTable rows={d.positions} portfolio={p} onRemove={onRemovePosition} />
            {/* The most personal table in the product: the reading rule travels with the rows, as on the insiders and fundamentals sections. */}
            <div className="px-4 py-2 text-[11px] text-muted-foreground">{t("pf.disclaimer")}</div>
          </div>
        )}
      </Section>

      <Section title={t("pf.moves")} hint={movesHint}>
        {moves.length === 0 ? (
          <div className="px-4 py-6 text-sm text-muted-foreground">{t("pf.moves.empty")}</div>
        ) : (
          <ul className="divide-y divide-border/60 text-sm">
            {moves.map((m) => <MoveRowItem key={m.symbol} m={m} market={fm} />)}
          </ul>
        )}
      </Section>

      {derived && (
        <Section title={t("pf.transactions")} hint={`${d.transactions!.length}`}>
          <TransactionsTable rows={d.transactions!} portfolio={p} onRemove={onRemoveTransaction} />
        </Section>
      )}
    </>
  )
}

function PositionsTable({ rows, portfolio: p, onRemove }: { rows: PortfolioPosition[]; portfolio: Portfolio; onRemove: (positionId: number) => void }) {
  const { t } = useI18n()
  const fm = flowMarket(p)
  const th = "px-2 py-2 text-right font-medium"
  const pnlTone = (v: number | null) => (v === null || v === 0 ? "text-muted-foreground" : v > 0 ? "text-positive" : "text-negative")
  return (
    <table className="w-full text-sm">
      <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
        <tr className="border-b border-border/60">
          <th className="px-4 py-2 text-left font-medium">{t("common.stock")}</th>
          <th className={th}>{t("common.quantity")}</th>
          <th className={cn(th, "hidden md:table-cell")}>{t("pf.avgCost")}</th>
          <th className={cn(th, "hidden md:table-cell")}>{t("pf.lastClose")}</th>
          <th className={th}>{t("pf.marketValue")}</th>
          <th className={th}>{t("pf.pnl")}</th>
          <th className={cn(th, "hidden sm:table-cell")}>{t("common.weight")}</th>
          <th className={cn(th, "hidden lg:table-cell")}>Smart Money</th>
          <th className={cn(th, "hidden xl:table-cell")}>{t("common.consensus")}</th>
          <th className={cn(th, "hidden xl:table-cell")}>{t("scores.crowding")}</th>
          <th className={cn(th, "hidden lg:table-cell")}>{t("pf.funds30")}</th>
          <th className={cn(th, "hidden lg:table-cell")}>{t("pf.insiders90")}</th>
          <th className="w-10" />
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.symbol} className="border-b border-border/40 last:border-0 hover:bg-accent/40">
            <td className="px-4 py-2 align-top">
              <Link to={`/stocks/${r.symbol}`} className="font-semibold hover:underline">{r.symbol}</Link>
              <div className="max-w-[11rem] truncate text-xs text-muted-foreground">{r.name}</div>
            </td>
            <td className="num whitespace-nowrap px-2 py-2 text-right align-top">{fmtQty(r.quantity)}</td>
            <td className="num hidden whitespace-nowrap px-2 py-2 text-right align-top md:table-cell">{fmtPrice(r.avg_cost, p.currency)}</td>
            <td className="num hidden whitespace-nowrap px-2 py-2 text-right align-top md:table-cell">
              {fmtPrice(r.last_close, p.currency)}
              <div className="text-[10px] text-muted-foreground">{r.close_date ? fmtDate(r.close_date) : t("pf.noClose")}</div>
            </td>
            <td className="num whitespace-nowrap px-2 py-2 text-right align-top">{fmtCompact(r.market_value, p.currency)}</td>
            <td className={cn("num whitespace-nowrap px-2 py-2 text-right align-top font-medium", pnlTone(r.pnl_value))}>
              {fmtMoney(r.pnl_value, fm)}
              <div className="text-[10px]">{r.pnl_pct === null ? "" : `${fmtNum(r.pnl_pct, 1, true)}%`}</div>
            </td>
            <td className="num hidden whitespace-nowrap px-2 py-2 text-right align-top sm:table-cell">{r.weight_pct === null ? "—" : `${fmtNum(r.weight_pct, 1)}%`}</td>
            <td className="hidden px-2 py-2 text-right align-top lg:table-cell"><ScorePill value={r.smart_money_score} size="sm" /></td>
            <td className="hidden px-2 py-2 text-right align-top xl:table-cell"><ScorePill value={r.consensus_score} size="sm" /></td>
            <td className="hidden px-2 py-2 text-right align-top xl:table-cell">{r.crowding_score === null ? <span className="text-muted-foreground">—</span> : <CrowdingPill score={r.crowding_score} size="sm" />}</td>
            <td className="num hidden px-2 py-2 text-right align-top lg:table-cell"><span className="text-positive">{r.funds_increasing_30d ?? "—"}</span> / <span className="text-negative">{r.funds_reducing_30d ?? "—"}</span></td>
            {/* Both markets (Form 4 on US, KAP on BIST), in the listing currency; "—" until the market's insider source has been read. */}
            <td className="hidden px-2 py-2 text-right align-top lg:table-cell"><Flow value={r.insiders_net_90d} market={p.market} /></td>
            {/* A derived row is rewritten from its transactions: it goes when they go, not by hand. */}
            <td className="px-2 py-2 text-right align-top">{!r.derived && <button type="button" onClick={() => onRemove(r.id)} className="text-muted-foreground hover:text-negative" aria-label={`${t("pf.removePosition")}: ${r.symbol}`}><Trash2 className="size-4" /></button>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** "ASELS · 4 fon artırdı · 1 fon azalttı · 1 yeni · +₺1.5M" then the funds behind it, as /moves prints them — descriptive, like the timeline. */
function MoveRowItem({ m, market }: { m: MoveRow; market: Market }) {
  const { t } = useI18n()
  const extra = [m.funds_new ? t("pf.moves.new", { n: m.funds_new }) : null, m.funds_exited ? t("pf.moves.exit", { n: m.funds_exited }) : null].filter(Boolean)
  return (
    <li className="px-4 py-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Link to={`/stocks/${m.symbol}`} className="font-semibold hover:underline">{m.symbol}</Link>
        <span className="text-muted-foreground">{t("pf.moves.line", { inc: m.funds_increasing, red: m.funds_reducing })}{extra.length ? ` · ${extra.join(" · ")}` : ""}</span>
        <Flow value={m.net_flow_value} market={market} className="text-xs" />
      </div>
      {m.parties?.length > 0 && (
        <div className="mt-1 flex flex-wrap items-center gap-1">
          {m.parties.map((x, i) => <PartyChip key={`${x.kind}:${x.code}:${i}`} party={x} market={market} />)}
          {m.party_count > m.parties.length && <span className="text-[11px] text-muted-foreground">{t("common.more", { n: m.party_count - m.parties.length })}</span>}
        </div>
      )}
    </li>
  )
}

/** "FUNDNAME · ADD +₺1.2M" in the activity's colour (the Moves page's chip); a GROUPED event shows its institution. */
function PartyChip({ party: p, market }: { party: MoveParty; market: Market }) {
  return (
    <Link to={p.kind === "fund" ? `/funds/${p.code}` : `/institutions/${p.code}`} title={`${p.name} · ${fmtDate(p.period_end)}`} className={cn("inline-flex max-w-full items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[11px] leading-4 hover:bg-accent/40", ACT[p.activity])}>
      <span className="max-w-[9rem] truncate text-foreground">{p.name}</span>
      <span className="opacity-60">·</span>
      <span className="font-semibold uppercase tracking-wider">{p.activity}</span>
      <span className="num">{p.delta_value === null ? fmtLots(p.delta_qty) : fmtMoney(p.delta_value, market)}</span>
      {p.confidence !== "INFERRED" && <ConfidenceBadge value={p.confidence} className="ml-0.5 px-1 py-0 text-[9px]" />}
    </Link>
  )
}

function TransactionsTable({ rows, portfolio: p, onRemove }: { rows: PortfolioTransaction[]; portfolio: Portfolio; onRemove: (id: number) => void }) {
  const { t } = useI18n()
  return (
    <table className="w-full text-sm">
      <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
        <tr className="border-b border-border/60">
          <th className="px-4 py-2 text-left font-medium">{t("pf.tradedAt")}</th>
          <th className="px-2 py-2 text-left font-medium">{t("common.stock")}</th>
          <th className="px-2 py-2 text-left font-medium">{t("pf.side")}</th>
          <th className="px-2 py-2 text-right font-medium">{t("common.quantity")}</th>
          <th className="px-2 py-2 text-right font-medium">{t("pf.price")}</th>
          <th className="hidden px-2 py-2 text-left font-medium sm:table-cell">{t("pf.note")}</th>
          <th className="w-10" />
        </tr>
      </thead>
      <tbody>
        {rows.map((x) => (
          <tr key={x.id} className="border-b border-border/40 last:border-0">
            <td className="num px-4 py-2 text-xs text-muted-foreground">{fmtDate(x.traded_at)}</td>
            <td className="px-2 py-2 font-semibold">{x.symbol}</td>
            <td className="px-2 py-2"><span className={cn("rounded-sm border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider", x.side === "BUY" ? "border-positive/40 text-positive" : "border-negative/40 text-negative")}>{t(x.side === "BUY" ? "pf.side.BUY" : "pf.side.SELL")}</span></td>
            <td className="num px-2 py-2 text-right">{fmtQty(x.quantity)}</td>
            <td className="num px-2 py-2 text-right">{fmtPrice(x.price, p.currency)}</td>
            <td className="hidden max-w-[14rem] truncate px-2 py-2 text-xs text-muted-foreground sm:table-cell">{x.note ?? ""}</td>
            <td className="px-2 py-2 text-right"><button type="button" onClick={() => onRemove(x.id)} className="text-muted-foreground hover:text-negative" aria-label={t("common.delete")}><Trash2 className="size-4" /></button></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/* ---------- dialogs ---------- */

/** Modal on the Radix primitive the palette uses: overlay, centred panel, a title and a ✕; Escape and the overlay close it. */
function Modal({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  const { t } = useI18n()
  return (
    <Dialog.Root open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-background/60 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content aria-describedby={undefined} className="fixed left-1/2 top-[12vh] z-50 w-[min(460px,calc(100vw-2rem))] -translate-x-1/2 rounded-lg border border-border bg-popover p-4 shadow-2xl outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95">
          <div className="mb-3 flex items-center justify-between">
            <Dialog.Title className="text-sm font-semibold">{title}</Dialog.Title>
            <Dialog.Close aria-label={t("common.close")} className="rounded-sm p-0.5 text-muted-foreground hover:text-foreground"><X className="size-4" /></Dialog.Close>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

/** Create or rename: the name, and for a new portfolio its market (fixed afterwards — positions are priced in its currency). */
function NameDialog({ open, mode, portfolio, market, onClose, onSaved }: { open: boolean; mode: "create" | "rename"; portfolio: Portfolio | null; market: Market; onClose: () => void; onSaved: (p: Portfolio) => void }) {
  const { t } = useI18n()
  const [name, setName] = useState("")
  const [mk, setMk] = useState<Market>(market)
  const [err, setErr] = useState<string | null>(null)
  // Reset on open only (not on every list refetch, which would wipe a name being typed).
  useEffect(() => { if (open) { setName(mode === "rename" ? portfolio?.name ?? "" : ""); setMk(market); setErr(null) } }, [open]) // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: () => (mode === "rename" && portfolio ? api.renamePortfolio(portfolio.id, name.trim()) : api.createPortfolio({ name: name.trim(), market: mk })),
    onSuccess: onSaved,
    onError: (e) => setErr(errorText(t, e)),
  })
  const submit = (e: FormEvent) => { e.preventDefault(); if (name.trim()) save.mutate() }
  return (
    <Modal open={open} onClose={onClose} title={mode === "rename" ? t("pf.rename") : t("pf.new")}>
      <form onSubmit={submit} className="space-y-3 text-sm">
        <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("field.name")}</div><input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder={t("pf.namePh")} maxLength={64} className={input} required /></label>
        {mode === "create" && (
          <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("market.label")}</div>
            <select value={mk} onChange={(e) => setMk(e.target.value as Market)} className={input}>
              <option value="TR">{t("market.name.TR")} · TRY</option>
              <option value="US">{t("market.name.US")} · USD</option>
            </select>
          </label>
        )}
        {err && <div role="alert" className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-xs">{err}</div>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" size="sm" disabled={save.isPending}>{mode === "rename" ? t("common.save") : t("pf.create")}</Button>
        </div>
      </form>
    </Modal>
  )
}

/**
 * Stock typeahead for the dialogs: the header search's endpoint (/search of the portfolio's market), stocks only, the
 * pick fills the field with the symbol. Typing a symbol outright and submitting works too — the API validates it.
 */
function SymbolField({ market, value, onChange, autoFocus }: { market: Market; value: string; onChange: (v: string) => void; autoFocus?: boolean }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [debounced, setDebounced] = useState("")
  const [active, setActive] = useState(0)
  const boxRef = useRef<HTMLDivElement>(null)
  const listId = useId()
  useEffect(() => { const id = setTimeout(() => setDebounced(value.trim()), 150); return () => clearTimeout(id) }, [value])
  const hits = useQuery({ queryKey: ["search", market, debounced], queryFn: () => api.search(market, debounced), enabled: open && debounced.length > 0, staleTime: 60_000, placeholderData: (prev) => prev })
  const rows = useMemo(() => (debounced ? (hits.data ?? []).filter((h) => h.kind === "stock") : []), [hits.data, debounced])
  useEffect(() => { setActive(0) }, [rows.length, debounced])
  useEffect(() => {
    const onDoc = (e: MouseEvent) => { if (!boxRef.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener("mousedown", onDoc)
    return () => document.removeEventListener("mousedown", onDoc)
  }, [])
  const pick = (i: number) => { const h = rows[i]; if (h) { onChange(h.label); setOpen(false) } }
  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open || rows.length === 0) return
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, rows.length - 1)) }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
    else if (e.key === "Enter") { e.preventDefault(); pick(active) }
    else if (e.key === "Escape") { e.stopPropagation(); setOpen(false) }
  }
  const showList = open && debounced.length > 0 && rows.length > 0
  return (
    <div ref={boxRef} className="relative">
      <input
        autoFocus={autoFocus}
        value={value}
        onChange={(e) => { onChange(e.target.value.toUpperCase()); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKey}
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-label={t("common.stock")}
        placeholder={t("pf.symbolPh")}
        autoComplete="off"
        className={cn(input, "font-mono uppercase")}
      />
      {showList && (
        <ul id={listId} role="listbox" className="absolute left-0 right-0 top-full z-50 mt-1 max-h-56 overflow-auto rounded-md border border-border bg-popover p-1 text-sm shadow-lg">
          {rows.map((h, i) => (
            <li key={h.key} role="option" aria-selected={i === active} onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); pick(i) }} className={cn("flex cursor-pointer items-center gap-2 rounded-[5px] px-2.5 py-1.5", i === active ? "bg-accent" : "")}>
              <span className="font-medium">{h.label}</span>
              <span className="truncate text-muted-foreground">{h.name}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

const EMPTY_POSITION = { symbol: "", quantity: "", avg_cost: "", opened_at: "", note: "" }

/** Direct entry: symbol, quantity, an optional average cost (no cost → no P&L), an optional open date and note. Upserts by symbol. */
function PositionDialog({ open, portfolio, onClose, onSaved }: { open: boolean; portfolio: Portfolio; onClose: () => void; onSaved: () => void }) {
  const { t } = useI18n()
  const [f, setF] = useState(EMPTY_POSITION)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { if (open) { setF(EMPTY_POSITION); setErr(null) } }, [open])
  const set = (k: keyof typeof EMPTY_POSITION) => (e: React.ChangeEvent<HTMLInputElement>) => { setF((s) => ({ ...s, [k]: e.target.value })); setErr(null) }
  const save = useMutation({
    mutationFn: () => api.upsertPosition(portfolio.id, { symbol: f.symbol.trim().toUpperCase(), quantity: num(f.quantity), avg_cost: f.avg_cost.trim() === "" ? null : num(f.avg_cost), opened_at: f.opened_at || null, note: f.note.trim() || null }),
    onSuccess: onSaved,
    onError: (e) => setErr(errorText(t, e)),
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    // Checked here before anything is sent: the API refuses the same, but the message should name the field.
    const problem = positionFormError(t, f)
    setErr(problem)
    if (!problem) save.mutate()
  }
  return (
    <Modal open={open} onClose={onClose} title={t("pf.addPosition")}>
      <form onSubmit={submit} className="space-y-3 text-sm" noValidate>
        <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("common.stock")}</div><SymbolField market={portfolio.market} value={f.symbol} onChange={(v) => { setF((s) => ({ ...s, symbol: v })); setErr(null) }} autoFocus /></label>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("common.quantity")}</div><input type="number" inputMode="decimal" step="any" min={0} value={f.quantity} onChange={set("quantity")} className={cn(input, "num")} /></label>
          <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("pf.avgCostOptional")} · {portfolio.currency}</div><input type="number" inputMode="decimal" step="any" min={0} value={f.avg_cost} onChange={set("avg_cost")} className={cn(input, "num")} /></label>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("pf.openedAt")}</div><input type="date" value={f.opened_at} onChange={set("opened_at")} max={today()} className={input} /></label>
          <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("pf.note")}</div><input value={f.note} onChange={set("note")} maxLength={256} className={input} /></label>
        </div>
        {err && <div role="alert" className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-xs">{err}</div>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" size="sm" disabled={save.isPending}>{t("common.add")}</Button>
        </div>
      </form>
    </Modal>
  )
}

const EMPTY_TX = { symbol: "", side: "BUY" as "BUY" | "SELL", quantity: "", price: "", traded_at: "", fee: "", note: "" }

/** A trade: once a portfolio has transactions its positions and average costs are derived from them (FIFO). */
function TransactionDialog({ open, portfolio, onClose, onSaved }: { open: boolean; portfolio: Portfolio; onClose: () => void; onSaved: () => void }) {
  const { t } = useI18n()
  const [f, setF] = useState(EMPTY_TX)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { if (open) { setF({ ...EMPTY_TX, traded_at: today() }); setErr(null) } }, [open])
  const set = (k: keyof typeof EMPTY_TX) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => { setF((s) => ({ ...s, [k]: e.target.value })); setErr(null) }
  const save = useMutation({
    mutationFn: () => api.addTransaction(portfolio.id, { symbol: f.symbol.trim().toUpperCase(), side: f.side, quantity: num(f.quantity), price: num(f.price), traded_at: f.traded_at, fee: f.fee.trim() === "" ? null : num(f.fee), note: f.note.trim() || null }),
    onSuccess: onSaved,
    onError: (e) => setErr(errorText(t, e)),
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const problem = transactionFormError(t, f)
    setErr(problem)
    if (!problem) save.mutate()
  }
  return (
    <Modal open={open} onClose={onClose} title={t("pf.addTx")}>
      <form onSubmit={submit} className="space-y-3 text-sm" noValidate>
        <div className="grid gap-3 sm:grid-cols-[1fr_120px]">
          <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("common.stock")}</div><SymbolField market={portfolio.market} value={f.symbol} onChange={(v) => { setF((s) => ({ ...s, symbol: v })); setErr(null) }} autoFocus /></label>
          <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("pf.side")}</div>
            <select value={f.side} onChange={set("side")} className={input}>
              <option value="BUY">{t("pf.side.BUY")}</option>
              <option value="SELL">{t("pf.side.SELL")}</option>
            </select>
          </label>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("common.quantity")}</div><input type="number" inputMode="decimal" step="any" min={0} value={f.quantity} onChange={set("quantity")} className={cn(input, "num")} /></label>
          <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("pf.price")} · {portfolio.currency}</div><input type="number" inputMode="decimal" step="any" min={0} value={f.price} onChange={set("price")} className={cn(input, "num")} /></label>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("pf.tradedAt")}</div><input type="date" value={f.traded_at} onChange={set("traded_at")} max={today()} className={input} /></label>
          <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("pf.fee")}</div><input type="number" inputMode="decimal" step="any" min={0} value={f.fee} onChange={set("fee")} className={cn(input, "num")} /></label>
        </div>
        <label className="block"><div className="mb-1 text-xs text-muted-foreground">{t("pf.note")}</div><input value={f.note} onChange={set("note")} maxLength={256} className={input} /></label>
        {err && <div role="alert" className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-xs">{err}</div>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" size="sm" disabled={save.isPending}>{t("common.add")}</Button>
        </div>
      </form>
    </Modal>
  )
}
