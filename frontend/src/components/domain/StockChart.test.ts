import { describe, expect, it } from "vitest"
import { mergeSeries } from "./StockChart"

describe("mergeSeries", () => {
  it("aligns both panels on one sorted date axis and carries holdings forward between reports", () => {
    const prices = [{ date: "2026-09-01", close: 10 }, { date: "2026-09-02", close: 11 }, { date: "2026-09-04", close: 12 }]
    const holdings = [{ date: "2026-09-02", quantity: 100, funds: 3 }, { date: "2026-09-03", quantity: 150, funds: 4 }]
    const rows = mergeSeries(prices, holdings)
    expect(rows.map((r) => r.t)).toEqual(["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"])
    expect(rows[0]).toMatchObject({ price: 10, qty: null, snap: false })     // before the first report: unknown
    expect(rows[1]).toMatchObject({ price: 11, qty: 100, funds: 3, snap: true })
    expect(rows[2]).toMatchObject({ price: null, qty: 150, funds: 4, snap: true })  // report on a non-trading day
    expect(rows[3]).toMatchObject({ price: 12, qty: 150, funds: 4, snap: false })   // carried forward, not a dot
  })
})
