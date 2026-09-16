import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import type { Market } from "./api"

const KEY = "instilens.market"
const Ctx = createContext<{ market: Market; setMarket: (m: Market) => void }>({ market: "TR", setMarket: () => {} })

export function MarketProvider({ children }: { children: ReactNode }) {
  const [market, setMarket] = useState<Market>(() => {
    try {
      return (localStorage.getItem(KEY) as Market) || "TR"
    } catch {
      return "TR"
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem(KEY, market)
    } catch { /* private mode */ }
  }, [market])
  return <Ctx.Provider value={{ market, setMarket }}>{children}</Ctx.Provider>
}

export const useMarket = () => useContext(Ctx)
