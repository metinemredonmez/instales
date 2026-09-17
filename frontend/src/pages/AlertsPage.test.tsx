import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { AlertRule, Market, Notification } from "@/lib/api"
import { LangProvider, translate } from "@/lib/i18n"
import { MarketProvider } from "@/lib/market"
import { setLocale } from "@/lib/format"

const rules = vi.fn<() => Promise<{ rule_types: string[]; rules: AlertRule[] }>>()
const notifications = vi.fn<() => Promise<Notification[]>>()
const addRule = vi.fn<(body: Record<string, unknown>) => Promise<{ id: number }>>()
vi.mock("@/lib/api", () => ({
  api: {
    rules: () => rules(),
    notifications: () => notifications(),
    addRule: (body: Record<string, unknown>) => addRule(body),
    removeRule: vi.fn(),
    evaluateAlerts: vi.fn(),
    markRead: vi.fn(),
  },
}))

import { AlertsPage, priceRuleError, ruleSuffix } from "./AlertsPage"

const RULE_TYPES = ["FUND_ACTIVITY", "FUND_EXIT", "INSIDER_BUY_CLUSTER", "KAP_TRANSACTION", "NEW_FUND_POSITION", "PRICE_ABOVE", "PRICE_BELOW", "SCORE_ABOVE", "SIGNAL"]
/** Owner-wide: BIST rules and a US one come back together, whichever market the header is on. */
const RULES: AlertRule[] = [
  { id: 1, rule_type: "PRICE_ABOVE", params: { price: 120, since: "2026-09-14" }, is_active: true, symbol: "ASELS", fund_code: null, market: "TR" },
  { id: 2, rule_type: "PRICE_BELOW", params: { price: 98.5, since: "2026-09-14" }, is_active: true, symbol: "THYAO", fund_code: null, market: "TR" },
  { id: 3, rule_type: "SCORE_ABOVE", params: { threshold: 80 }, is_active: true, symbol: "GARAN", fund_code: null, market: "TR" },
  { id: 4, rule_type: "NEW_FUND_POSITION", params: {}, is_active: true, symbol: null, fund_code: "TMV", market: "TR" },
  { id: 5, rule_type: "PRICE_ABOVE", params: { price: 150, since: "2026-09-14" }, is_active: true, symbol: "NVDA", fund_code: null, market: "US" },
]

function setup(market: Market = "TR", lang: "tr" | "en" = "tr") {
  localStorage.setItem("instilens.market", market)
  localStorage.setItem("instilens.lang", lang)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <LangProvider>
        <MarketProvider>
          <MemoryRouter><AlertsPage /></MemoryRouter>
        </MarketProvider>
      </LangProvider>
    </QueryClientProvider>,
  )
  return userEvent.setup()
}

const form = () => within(screen.getByText("Yeni kural").closest("section")!)
const activeRules = () => within(screen.getByText("Aktif kurallar").closest("section")!)

describe("AlertsPage price rules", () => {
  beforeEach(() => {
    rules.mockReset(); notifications.mockReset(); addRule.mockReset()
    rules.mockResolvedValue({ rule_types: RULE_TYPES, rules: RULES })
    notifications.mockResolvedValue([])
    addRule.mockResolvedValue({ id: 9 })
  })
  afterEach(() => { localStorage.removeItem("instilens.market"); localStorage.removeItem("instilens.lang"); setLocale("tr") })

  it("lists price rules as 'ASELS > 120 ₺' with the rule's own currency and only the decimals the threshold has", async () => {
    setup()
    expect(await activeRules().findByRole("link", { name: "ASELS" })).toHaveAttribute("href", "/stocks/ASELS")
    const line = (sym: string) => activeRules().getByRole("link", { name: sym }).nextElementSibling!.textContent
    expect(line("ASELS")).toBe("Fiyat eşiğin üstüne çıkınca > 120 ₺")
    expect(line("THYAO")).toBe("Fiyat eşiğin altına inince < 98,5 ₺")
    expect(line("GARAN")).toBe("Smart Money Score eşiği ≥ 80")
    expect(line("TMV")).toBe("Yeni fon pozisyonu")
    expect(line("NVDA")).toBe("Fiyat eşiğin üstüne çıkınca > 150 $")   // a US rule reads in dollars on the BIST view too
  })

  it("offers both price rules in the type select and shows the price field, in the market currency, when one is picked", async () => {
    const user = setup()
    const select = await form().findByRole("combobox")
    const labels = within(select).getAllByRole("option").map((o) => o.textContent)
    expect(labels).toContain("Fiyat eşiğin üstüne çıkınca")
    expect(labels).toContain("Fiyat eşiğin altına inince")
    expect(labels).not.toContain("İçeriden alım kümesi (Form 4)")   // BIST: no Form 4 rule
    expect(form().queryByText("Fiyat eşiği (TRY)")).toBeNull()
    await user.selectOptions(select, "PRICE_ABOVE")
    expect(form().getByText("Fiyat eşiği (TRY)")).toBeInTheDocument()
    expect(form().getByText("günlük kapanışa göre değerlendirilir; her geçişte bir kez bildirilir")).toBeInTheDocument()
  })

  it("refuses an empty, zero or negative price without sending anything", async () => {
    const user = setup()
    const select = await form().findByRole("combobox")
    await user.selectOptions(select, "PRICE_ABOVE")
    const [subject, price] = form().getAllByRole("textbox").concat(form().getAllByRole("spinbutton"))
    await user.type(subject, "ASELS")
    await user.click(form().getByRole("button", { name: "Kural ekle" }))
    expect(screen.getByRole("alert").textContent).toBe("Fiyat 0'dan büyük olmalı.")
    await user.type(price, "0")
    await user.click(form().getByRole("button", { name: "Kural ekle" }))
    expect(screen.getByRole("alert").textContent).toBe("Fiyat 0'dan büyük olmalı.")
    await user.clear(price)
    await user.type(price, "-5")
    await user.click(form().getByRole("button", { name: "Kural ekle" }))
    expect(screen.getByRole("alert").textContent).toBe("Fiyat 0'dan büyük olmalı.")
    expect(addRule).not.toHaveBeenCalled()
  })

  it("sends a valid price rule with {price} in params for the stock and clears the form", async () => {
    const user = setup()
    const select = await form().findByRole("combobox")
    await user.selectOptions(select, "PRICE_BELOW")
    await user.type(form().getByRole("textbox"), "asels")
    const price = form().getByRole("spinbutton")
    await user.type(price, "120.5")
    await user.click(form().getByRole("button", { name: "Kural ekle" }))
    await waitFor(() => expect(addRule).toHaveBeenCalledWith({ symbol: "ASELS", market: "TR", rule_type: "PRICE_BELOW", params: { price: 120.5 } }))
    await waitFor(() => expect(price).toHaveValue(null))
    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("offers the price rules for stocks only: a fund code greys them out and a choice made earlier is refused", async () => {
    const user = setup()
    const select = await form().findByRole("combobox")
    const priceOptions = () => within(select).getAllByRole("option").filter((o) => o.textContent?.startsWith("Fiyat eşiğin"))
    await user.selectOptions(select, "PRICE_ABOVE")
    const subject = form().getByRole("textbox")
    await user.type(subject, "TMV")
    expect(priceOptions()).toHaveLength(2)
    expect(priceOptions().every((o) => (o as HTMLOptionElement).disabled)).toBe(true)
    // A fourth letter makes it a stock again (the options come back, never removed, so nothing on the form jumps).
    await user.type(subject, "A")
    expect(priceOptions().every((o) => !(o as HTMLOptionElement).disabled)).toBe(true)
    await user.type(subject, "{Backspace}")
    await user.type(form().getByRole("spinbutton"), "120")
    await user.click(form().getByRole("button", { name: "Kural ekle" }))
    expect(screen.getByRole("alert").textContent).toBe("Fiyat kuralları yalnızca hisseler için; fon kodu için başka bir kural seç.")
    expect(addRule).not.toHaveBeenCalled()
  })

  it("prices a US rule in dollars and keeps a BIST rule in lira on the US view; the form takes the active market's currency", async () => {
    const user = setup("US")
    expect(await activeRules().findByRole("link", { name: "NVDA" })).toBeInTheDocument()
    const line = (sym: string) => activeRules().getByRole("link", { name: sym }).nextElementSibling!.textContent
    expect(line("NVDA")).toBe("Fiyat eşiğin üstüne çıkınca > 150 $")
    expect(line("ASELS")).toBe("Fiyat eşiğin üstüne çıkınca > 120 ₺")
    await user.selectOptions(await form().findByRole("combobox"), "PRICE_ABOVE")
    expect(form().getByText("Fiyat eşiği (USD)")).toBeInTheDocument()
  })
})

describe("priceRuleError / ruleSuffix", () => {
  const t = (k: Parameters<typeof translate>[1], v?: Parameters<typeof translate>[2]) => translate("en", k, v)

  it("validates the typed price and the subject's shape", () => {
    expect(priceRuleError(t, "TR", "ASELS", "120")).toBeNull()
    expect(priceRuleError(t, "TR", "ASELS", "120,5")).toBeNull()
    expect(priceRuleError(t, "TR", "ASELS", "")).toBe("Price must be greater than 0.")
    expect(priceRuleError(t, "TR", "ASELS", "0")).toBe("Price must be greater than 0.")
    expect(priceRuleError(t, "TR", "ASELS", "abc")).toBe("Price must be greater than 0.")
    expect(priceRuleError(t, "TR", "TMV", "120")).toBe("Price rules are for stocks only; pick another rule for a fund code.")
    expect(priceRuleError(t, "US", "TMV", "120")).toBeNull()   // three letters on the US market is a stock
  })

  it("prints the threshold the way the notification does, in the rule's market, and a dash for a rule without a usable price", () => {
    setLocale("en")
    expect(ruleSuffix({ id: 1, rule_type: "PRICE_ABOVE", params: { price: 1234.56 }, is_active: true, symbol: "NVDA", fund_code: null, market: "US" })).toBe(" > 1,234.56 $")
    expect(ruleSuffix({ id: 1, rule_type: "PRICE_ABOVE", params: { price: 1234.56 }, is_active: true, symbol: "ASELS", fund_code: null, market: "TR" })).toBe(" > 1,234.56 ₺")
    expect(ruleSuffix({ id: 1, rule_type: "PRICE_BELOW", params: {}, is_active: true, symbol: "NVDA", fund_code: null, market: "US" })).toBe(" < —")
    expect(ruleSuffix({ id: 1, rule_type: "FUND_EXIT", params: {}, is_active: true, symbol: null, fund_code: "TMV", market: "TR" })).toBe("")
  })
})
