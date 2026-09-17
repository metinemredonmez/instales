import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("./auth", () => ({ getToken: () => "tok" }))

import { ApiError, PlanLimitError, api, isNotConfigured, isPlanLimit } from "./api"

const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>()
const answer = (status: number, body: unknown) => fetchMock.mockResolvedValueOnce(new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json" } }))

describe("api error handling", () => {
  beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal("fetch", fetchMock) })
  afterEach(() => vi.unstubAllGlobals())

  it("turns a 402 plan_limit into a PlanLimitError with the fields, and announces it on instilens:plan-limit", async () => {
    const seen: unknown[] = []
    const on = (e: Event) => seen.push((e as CustomEvent).detail)
    window.addEventListener("instilens:plan-limit", on)
    answer(402, { detail: "plan_limit", feature: "portfolio", plan: "FREE", limit: 0, upgrade: "PRO" })
    const err = await api.portfolios().catch((e: unknown) => e)
    window.removeEventListener("instilens:plan-limit", on)
    expect(isPlanLimit(err)).toBe(true)
    const e = err as PlanLimitError
    expect(e.status).toBe(402)
    expect(e.feature).toBe("portfolio")
    expect(e.plan).toBe("FREE")
    expect(e.limit).toBe(0)
    expect(e.upgrade).toBe("PRO")
    expect(e instanceof ApiError).toBe(true)
    expect(seen).toEqual([e])
    // The request carried the bearer and went to the portfolios route.
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/portfolios", expect.objectContaining({ headers: { authorization: "Bearer tok" } }))
  })

  it("reads the same fields when FastAPI nests them under detail, and falls back to PRO for an unknown upgrade", async () => {
    answer(402, { detail: { detail: "plan_limit", feature: "alert_rules", plan: "PRO", limit: 50, upgrade: "PRO_PLUS" } })
    const a = await api.rules().catch((e: unknown) => e) as PlanLimitError
    expect(isPlanLimit(a)).toBe(true)
    expect(a.feature).toBe("alert_rules")
    expect(a.limit).toBe(50)
    expect(a.upgrade).toBe("PRO_PLUS")
    answer(402, { detail: "plan_limit", feature: "tts", plan: "FREE" })
    const b = await api.ttsStatus().catch((e: unknown) => e) as PlanLimitError
    expect(b.limit).toBeNull()
    expect(b.upgrade).toBe("PRO")
  })

  it("a 402 that is not a plan limit stays an ApiError; other statuses keep the '<status> <body>' message pages match on", async () => {
    answer(402, { detail: "payment required" })
    const a = await api.portfolios().catch((e: unknown) => e)
    expect(isPlanLimit(a)).toBe(false)
    expect(a).toBeInstanceOf(ApiError)
    expect((a as ApiError).detail).toBe("payment required")
    answer(404, { detail: "not found" })
    const b = await api.fund("XYZ").catch((e: unknown) => e) as ApiError
    expect(b.status).toBe(404)
    expect(b.message).toMatch(/^404\b/)
    expect(b.detail).toBe("not found")
    fetchMock.mockResolvedValueOnce(new Response("plain text", { status: 500 }))
    const c = await api.fund("XYZ").catch((e: unknown) => e) as ApiError
    expect(c.message).toBe("500 plain text")
    expect(c.detail).toBe("plain text")
  })

  it("503 from billing is 'payments not configured', never a checkout", async () => {
    answer(503, { detail: "payments not configured" })
    const e = await api.billingCheckout("PRO").catch((x: unknown) => x)
    expect(isNotConfigured(e)).toBe(true)
    expect(isPlanLimit(e)).toBe(false)
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/billing/checkout", expect.objectContaining({ method: "POST", body: JSON.stringify({ plan: "PRO" }) }))
  })

  it("401 logs the session out and 204 resolves to nothing", async () => {
    const out = vi.fn()
    window.addEventListener("instilens:unauthorized", out)
    answer(401, { detail: "expired" })
    await expect(api.portfolios()).rejects.toThrow()
    window.removeEventListener("instilens:unauthorized", out)
    expect(out).toHaveBeenCalledTimes(1)
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
    await expect(api.deletePortfolio(1)).resolves.toBeUndefined()
  })
})
