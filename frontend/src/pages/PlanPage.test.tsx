import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { BillingMe, BillingPlans, Org, PlanCode } from "@/lib/api"
import type { User } from "@/lib/auth"
import { LangProvider, translate } from "@/lib/i18n"
import { setLocale } from "@/lib/format"

const billingPlans = vi.fn<() => Promise<BillingPlans>>()
const billingMe = vi.fn<() => Promise<BillingMe>>()
const billingCheckout = vi.fn<(plan: PlanCode) => Promise<{ url: string }>>()
const billingPortal = vi.fn<(flow?: string) => Promise<{ url: string }>>()
const org = vi.fn<() => Promise<Org | null>>()
const orgInvite = vi.fn<(email: string) => Promise<{ member_id: number; email: string; sent: boolean; org: Org }>>()
const orgRemoveMember = vi.fn<(id: number) => Promise<void>>()
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  api: {
    billingPlans: () => billingPlans(),
    billingMe: () => billingMe(),
    billingCheckout: (p: PlanCode) => billingCheckout(p),
    billingPortal: (flow?: string) => billingPortal(flow),
    org: () => org(),
    orgInvite: (e: string) => orgInvite(e),
    orgRemoveMember: (id: number) => orgRemoveMember(id),
    createOrg: vi.fn(), deleteOrg: vi.fn(),
  },
}))
let user: User | null = null
const refreshUser = vi.fn<() => Promise<void>>()
vi.mock("@/lib/auth", async (orig) => ({ ...(await orig<typeof import("@/lib/auth")>()), useAuth: () => ({ user, refreshUser }) }))

import { ApiError } from "@/lib/api"
import { PlanPage, isLiveStripe, matrixRows, orgAllowed, priceLabel, taxLabel } from "./PlanPage"

const PLANS: BillingPlans = {
  configured: false,
  currency: "usd",
  plans: [
    { code: "PRO_PLUS", price: { id: "price_plus", amount: 29, currency: "usd", interval: "month" }, features: { watchlist_items: 500, alert_rules: 500, ai_research_per_day: 300, portfolio: true, portfolios: 5, tts: true, push: true, org_seats: 10 } },
    { code: "FREE", price: null, features: { watchlist_items: 10, alert_rules: 3, ai_research_per_day: 5, portfolio: false, portfolios: 0, tts: false, push: false, org_seats: 0 } },
    { code: "PRO", price: { id: "price_pro", amount: 9, currency: "usd", interval: "month" }, features: { watchlist_items: 100, alert_rules: 50, ai_research_per_day: 50, portfolio: true, portfolios: 1, tts: true, push: true, org_seats: 0 } },
  ],
}
const FREE_USER: User = { id: 3, email: "f@x.com", name: "Free", plan: "FREE", role: "USER", plans_enforced: true, features: { portfolio: false, org_seats: 0 } }

function setup(path = "/plan") {
  localStorage.setItem("instilens.lang", "tr")
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <LangProvider><MemoryRouter initialEntries={[path]}><PlanPage /></MemoryRouter></LangProvider>
    </QueryClientProvider>,
  )
  return userEvent.setup()
}

describe("PlanPage", () => {
  const assign = vi.fn()
  beforeEach(() => {
    billingPlans.mockReset(); billingMe.mockReset(); billingCheckout.mockReset(); billingPortal.mockReset(); org.mockReset(); orgInvite.mockReset(); orgRemoveMember.mockReset(); assign.mockReset(); refreshUser.mockReset()
    billingPlans.mockResolvedValue(PLANS)
    billingMe.mockResolvedValue({ plan: "FREE", source: "own", subscription: null })
    org.mockResolvedValue(null)
    orgRemoveMember.mockResolvedValue(undefined)
    refreshUser.mockResolvedValue(undefined)
    user = FREE_USER
    // jsdom cannot navigate; the page hands Stripe's URL to location.assign.
    vi.stubGlobal("location", { ...window.location, assign })
  })
  afterEach(() => { vi.unstubAllGlobals(); localStorage.removeItem("instilens.lang"); setLocale("tr") })

  it("renders the matrix in plan order with prices, marks the current plan, and shows the calm 'not open yet' state with the upgrade buttons off", async () => {
    setup()
    const table = await screen.findByRole("table")
    const heads = within(table).getAllByRole("columnheader").map((h) => h.textContent)
    expect(heads).toEqual(["", "Ücretsizücretsiz", "Pro$9,00/ay", "Pro+$29,00/ay"])
    const row = (label: string) => within(table).getByRole("rowheader", { name: label }).closest("tr")!
    expect(within(row("Liste başına hisse")).getAllByRole("cell").map((c) => c.textContent)).toEqual(["10", "100", "500"])
    expect(within(row("Portföy takibi")).getAllByRole("cell").map((c) => c.textContent)).toEqual(["yok", "dahil", "dahil"])
    expect(within(row("Kurum koltuğu")).getAllByRole("cell").map((c) => c.textContent)).toEqual(["yok", "yok", "10"])
    // Only what the product delivers is on the table: no row for a feature the API does not send.
    expect(within(table).queryByRole("rowheader", { name: /Takip listesi|Öncelikli akış/ })).toBeNull()
    expect(within(table).getByText("senin planın")).toBeInTheDocument()
    expect(screen.getByText("Mevcut plan").nextElementSibling!.textContent).toBe("Ücretsiz")
    expect(screen.getByText("kendi aboneliğin")).toBeInTheDocument()
    // Payments not configured: the state is said once, the two upgrade buttons stay but are disabled — no stand-in checkout.
    expect(screen.getByText("Ödeme henüz açık değil.")).toBeInTheDocument()
    const buttons = within(table).getAllByRole("button", { name: "Yükselt" })
    expect(buttons).toHaveLength(2)
    expect(buttons.every((b) => (b as HTMLButtonElement).disabled)).toBe(true)
    expect(screen.queryByRole("button", { name: /Faturalandırmayı yönet/ })).toBeNull()
    // FREE has no organisation.
    expect(screen.getByText("Kurum özelliği Pro+ ile açılır.")).toBeInTheDocument()
    expect(org).not.toHaveBeenCalled()
  })

  it("with Stripe configured, Upgrade opens the Checkout Session the API returns; a 503 later flips to the not-configured state", async () => {
    billingPlans.mockResolvedValue({ ...PLANS, configured: true })
    billingCheckout.mockResolvedValue({ url: "https://checkout.stripe.com/c/pay/cs_test_1" })
    const u = setup()
    const table = await screen.findByRole("table")
    expect(screen.queryByText("Ödeme henüz açık değil.")).toBeNull()
    const [pro, proPlus] = within(table).getAllByRole("button", { name: "Yükselt" })
    expect((pro as HTMLButtonElement).disabled).toBe(false)
    await u.click(pro)
    await waitFor(() => expect(billingCheckout).toHaveBeenCalledWith("PRO"))
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://checkout.stripe.com/c/pay/cs_test_1"))
    billingCheckout.mockRejectedValue(new ApiError(503, "payments not configured", "503 payments not configured"))
    await u.click(proPlus)
    expect(await screen.findByText("Ödeme henüz açık değil.")).toBeInTheDocument()
    expect(within(table).getAllByRole("button", { name: "Yükselt" }).every((b) => (b as HTMLButtonElement).disabled)).toBe(true)
    expect(assign).toHaveBeenCalledTimes(1)
  })

  it("a Pro subscriber on Stripe gets the Billing Portal button and their period end; Pro+ sees the organisation section", async () => {
    user = { ...FREE_USER, plan: "PRO_PLUS", features: { org_seats: 10 } }
    billingPlans.mockResolvedValue({ ...PLANS, configured: true })
    billingMe.mockResolvedValue({ plan: "PRO_PLUS", source: "own", subscription: { id: 1, plan: "PRO_PLUS", status: "ACTIVE", provider: "stripe", current_period_end: "2026-10-16", cancel_at_period_end: false } })
    billingPortal.mockResolvedValue({ url: "https://billing.stripe.com/p/session/1" })
    const acme: Org = { id: 1, name: "Acme", owner_user_id: 3, is_owner: true, plan: "PRO_PLUS", seats: 10, seats_used: 2, created_at: "2026-09-01", members: [{ id: 1, user_id: 3, role: "OWNER", email: "f@x.com", name: "Free", accepted_at: "2026-09-01", pending: false }, { id: 2, user_id: null, role: "MEMBER", email: "m@x.com", name: null, accepted_at: null, pending: true }] }
    org.mockResolvedValue(acme)
    const u = setup()
    const portal = await screen.findByRole("button", { name: /Faturalandırmayı yönet/ })
    expect(screen.getByText(/aktif · dönem sonu 16 Eki 2026/)).toBeInTheDocument()
    await u.click(portal)
    await waitFor(() => expect(billingPortal).toHaveBeenCalledWith(undefined))
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://billing.stripe.com/p/session/1"))
    // No upgrade button above Pro+; the org section lists the members, the pending one marked, with the owner's controls.
    expect(screen.queryByRole("button", { name: "Yükselt" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Planı değiştir" })).toBeNull()
    expect(await screen.findByText("Acme")).toBeInTheDocument()
    expect(screen.getByText("2/10 koltuk")).toBeInTheDocument()
    expect(screen.getByText("bekliyor")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Üyeyi kaldır: m@x.com" })).toBeInTheDocument()
    // An invitation answers with the seat and whether the mail went out; a deployment without SMTP says so instead of "sent".
    orgInvite.mockResolvedValue({ member_id: 3, email: "n@x.com", sent: false, org: acme })
    await u.type(screen.getByPlaceholderText("e-posta adresi"), "n@x.com")
    await u.click(screen.getByRole("button", { name: "Davet et" }))
    await waitFor(() => expect(orgInvite).toHaveBeenCalledWith("n@x.com"))
    expect(await screen.findByText(/Koltuk ayrıldı \(n@x.com\)/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Kurumu kapat" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Kurumdan ayrıl" })).toBeNull()  // the owner's seat cannot go
  })

  it("a live Stripe subscriber changes plan through the portal's subscription_update flow, never a second checkout", async () => {
    user = { ...FREE_USER, plan: "PRO" }
    billingPlans.mockResolvedValue({ ...PLANS, configured: true })
    billingMe.mockResolvedValue({ plan: "PRO", source: "own", subscription: { id: 1, plan: "PRO", status: "ACTIVE", provider: "stripe", current_period_end: "2026-10-16", cancel_at_period_end: false } })
    billingPortal.mockResolvedValue({ url: "https://billing.stripe.com/p/session/2" })
    const u = setup()
    const table = await screen.findByRole("table")
    await waitFor(() => expect(within(table).queryByRole("button", { name: "Planı değiştir" })).not.toBeNull())
    expect(within(table).queryByRole("button", { name: "Yükselt" })).toBeNull()
    await u.click(within(table).getByRole("button", { name: "Planı değiştir" }))
    await waitFor(() => expect(billingPortal).toHaveBeenCalledWith("subscription_update"))
    expect(billingCheckout).not.toHaveBeenCalled()
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://billing.stripe.com/p/session/2"))
    // A manual grant (no Stripe row) still checks out; a 409 from the API reads as the portal hint.
    billingMe.mockResolvedValue({ plan: "PRO", source: "manual", subscription: { id: 2, plan: "PRO", status: "ACTIVE", provider: "manual", current_period_end: null, cancel_at_period_end: false } })
    billingCheckout.mockRejectedValue(new ApiError(409, "subscription_exists", "409 subscription_exists"))
    // fresh render: the query cache is per setup()
    document.body.innerHTML = ""
    const u2 = setup()
    const t2 = await screen.findByRole("table")
    await u2.click(await within(t2).findByRole("button", { name: "Yükselt" }))
    await waitFor(() => expect(billingCheckout).toHaveBeenCalledWith("PRO_PLUS"))
    expect(await screen.findByText(/Zaten aktif bir aboneliğin var/)).toBeInTheDocument()
  })

  it("back from Stripe: success says so, blocks another checkout and re-reads the account until the plan lands; cancel is a calm note", async () => {
    billingPlans.mockResolvedValue({ ...PLANS, configured: true })
    billingMe
      .mockResolvedValueOnce({ plan: "FREE", source: "own", subscription: null })
      .mockResolvedValue({ plan: "PRO", source: "own", subscription: { id: 1, plan: "PRO", status: "ACTIVE", provider: "stripe", current_period_end: "2026-10-16", cancel_at_period_end: false } })
    setup("/plan?checkout=success&session_id=cs_test_1")
    expect(await screen.findByText("Ödeme alındı; planın birkaç saniye içinde güncellenir.")).toBeInTheDocument()
    const table = await screen.findByRole("table")
    // The webhook has not landed on the first read: the upgrade buttons wait rather than start a second checkout.
    await waitFor(() => expect(billingMe).toHaveBeenCalledTimes(1))
    expect(within(table).getAllByRole("button", { name: "Yükselt" }).every((b) => (b as HTMLButtonElement).disabled)).toBe(true)
    // The second read (the poll) has the subscription: the session is refreshed and the plan shows.
    await waitFor(() => expect(billingMe.mock.calls.length).toBeGreaterThanOrEqual(2), { timeout: 6000 })
    await waitFor(() => expect(refreshUser).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText("Mevcut plan").nextElementSibling!.textContent).toBe("Pro"))
    expect(within(table).queryByRole("button", { name: "Yükselt" })).toBeNull()  // Pro+ above a live Stripe Pro is a plan change
    expect(within(table).getByRole("button", { name: "Planı değiştir" })).toBeInTheDocument()
    document.body.innerHTML = ""
    billingMe.mockReset(); billingMe.mockResolvedValue({ plan: "FREE", source: "own", subscription: null })
    setup("/plan?checkout=cancel")
    expect(await screen.findByText("Ödeme tamamlanmadı; planın değişmedi.")).toBeInTheDocument()
    const t2 = await screen.findByRole("table")
    await waitFor(() => expect(within(t2).getAllByRole("button", { name: "Yükselt" }).every((b) => !(b as HTMLButtonElement).disabled)).toBe(true))
  }, 15000)

  it("a member sees 'leave' on their own row and nothing else to manage", async () => {
    user = { ...FREE_USER, id: 9, email: "m@x.com", plan: "PRO_PLUS", plan_source: "org", features: { org_seats: 10 } }
    billingMe.mockResolvedValue({ plan: "PRO_PLUS", source: "org", subscription: null, org: { id: 1, name: "Acme", plan: "PRO_PLUS", owner: false } })
    org.mockResolvedValue({ id: 1, name: "Acme", owner_user_id: 3, is_owner: false, plan: "PRO_PLUS", seats: 10, seats_used: 2, created_at: "2026-09-01", members: [{ id: 1, user_id: 3, role: "OWNER", email: "f@x.com", name: "Free", accepted_at: "2026-09-01", pending: false }, { id: 2, user_id: 9, role: "MEMBER", email: "m@x.com", name: "M", accepted_at: "2026-09-02", pending: false }] })
    vi.stubGlobal("confirm", vi.fn(() => true))
    const u = setup()
    const leave = await screen.findByRole("button", { name: /Kurumdan ayrıl/ })
    expect(screen.queryByRole("button", { name: "Davet et" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Kurumu kapat" })).toBeNull()
    await u.click(leave)
    await waitFor(() => expect(orgRemoveMember).toHaveBeenCalledWith(2))
  })
})

describe("matrixRows / priceLabel / orgAllowed", () => {
  const t = (k: Parameters<typeof translate>[1], v?: Parameters<typeof translate>[2]) => translate("en", k, v)
  it("orders the columns FREE → PRO → PRO_PLUS and takes the rows from the API in first-seen order", () => {
    const { columns, rows } = matrixRows(PLANS)
    expect(columns.map((c) => c.code)).toEqual(["FREE", "PRO", "PRO_PLUS"])
    expect(rows.slice(0, 3)).toEqual(["watchlist_items", "alert_rules", "ai_research_per_day"])
    expect(matrixRows({ configured: false, currency: "usd", plans: [] }).columns).toEqual([])
  })
  it("prices: free for FREE or zero, the amount with the currency and /mo, a dash when unset", () => {
    setLocale("en")
    expect(priceLabel(t, { code: "FREE", price: null, features: {} }, "usd")).toBe("free")
    expect(priceLabel(t, { code: "PRO", price: { id: "p", amount: 9, currency: "usd", interval: "month" }, features: {} }, "usd")).toBe("$9.00/mo")
    expect(priceLabel(t, { code: "PRO", price: { id: "p", amount: 199, currency: "try", interval: "month" }, features: {} }, "usd")).toBe("₺199.00/mo")   // the Price's own currency wins
    expect(priceLabel(t, { code: "PRO", price: { id: "p", amount: 12, currency: "", interval: null }, features: {} }, "eur")).toBe("€12.00/mo")
    expect(priceLabel(t, { code: "PRO_PLUS", price: null, features: {} }, "usd")).toBe("—")
    expect(priceLabel(t, { code: "PRO_PLUS", price: { id: "p", amount: null, currency: "usd", interval: null }, features: {} }, "usd")).toBe("—")
  })
  it("tax status comes from the Price's own tax_behavior; a live Stripe row is what makes an upgrade a plan change", () => {
    expect(taxLabel(t, { code: "PRO", price: { id: "p", amount: 9, currency: "usd", interval: "month", tax_behavior: "inclusive" }, features: {} })).toBe("VAT included")
    expect(taxLabel(t, { code: "PRO", price: { id: "p", amount: 9, currency: "usd", interval: "month", tax_behavior: "exclusive" }, features: {} })).toBe("VAT not included")
    expect(taxLabel(t, { code: "PRO", price: { id: "p", amount: 9, currency: "usd", interval: "month", tax_behavior: "unspecified" }, features: {} })).toBeNull()
    expect(taxLabel(t, { code: "PRO", price: { id: "p", amount: 9, currency: "usd", interval: "month" }, features: {} })).toBeNull()
    const sub = { id: 1, plan: "PRO" as const, provider: "stripe" as const, current_period_end: null, cancel_at_period_end: false }
    expect(isLiveStripe({ ...sub, status: "ACTIVE" })).toBe(true)
    expect(isLiveStripe({ ...sub, status: "PAST_DUE" })).toBe(true)
    expect(isLiveStripe({ ...sub, status: "CANCELED" })).toBe(false)
    expect(isLiveStripe({ ...sub, status: "INCOMPLETE" })).toBe(false)
    expect(isLiveStripe({ ...sub, status: "ACTIVE", provider: "manual" })).toBe(false)
    expect(isLiveStripe(null)).toBe(false)
  })
  it("organisations: admins always, else the matrix's org_seats, else the plan itself", () => {
    const base: User = { id: 1, email: "a@x", name: "a", plan: "FREE" }
    expect(orgAllowed(null, "PRO_PLUS")).toBe(false)
    expect(orgAllowed({ ...base, role: "ADMIN" }, "FREE")).toBe(true)
    expect(orgAllowed({ ...base, features: { org_seats: 0 } }, "PRO_PLUS")).toBe(false)
    expect(orgAllowed({ ...base, features: { org_seats: 10 } }, "FREE")).toBe(true)
    expect(orgAllowed(base, "PRO_PLUS")).toBe(true)
    expect(orgAllowed(base, "PRO")).toBe(false)
  })
})
