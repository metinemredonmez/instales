import { useCallback, useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api, type SearchHit } from "@/lib/api"
import { useMarket } from "@/lib/market"

/** From this many characters on, the header box and the palette add a last row that opens the full-text page. */
export const TEXT_MIN = 3

/**
 * Typeahead state shared by the header SearchBox and the ⌘K palette: debounced /search of the active market,
 * plus the "open this hit" navigation with the shape-based fallback when nothing matched, and the jump to the
 * full-text page (/search?q=) for a query long enough to be worth one.
 */
export function useSearch() {
  const navigate = useNavigate()
  const { market } = useMarket()
  const [q, setQ] = useState("")
  const [debounced, setDebounced] = useState("")

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 150)
    return () => clearTimeout(t)
  }, [q])

  const hits = useQuery({
    queryKey: ["search", market, debounced],
    queryFn: () => api.search(market, debounced),
    enabled: debounced.length > 0,
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  })
  const rows: SearchHit[] = debounced ? hits.data ?? [] : []

  const reset = useCallback(() => { setQ(""); setDebounced("") }, [])

  /** Navigate to a hit — or, with none, to the most likely route by shape (TR + 3 letters → fund code). Returns false when there is nothing to open. */
  const go = useCallback((hit: SearchHit | undefined): boolean => {
    const s = q.trim().toUpperCase()
    if (!hit && !s) return false
    navigate(hit ? hit.href : market === "TR" && s.length === 3 ? `/funds/${s}` : `/stocks/${s}`)
    reset()
    return true
  }, [q, market, navigate, reset])

  /** Open the full-text page for the typed query. Returns false when it is shorter than TEXT_MIN (nothing opened). */
  const goText = useCallback((): boolean => {
    const s = q.trim()
    if (s.length < TEXT_MIN) return false
    navigate(`/search?q=${encodeURIComponent(s)}`)
    reset()
    return true
  }, [q, navigate, reset])

  return { q, setQ, debounced, rows, fetching: hits.isFetching, go, goText, reset, market }
}
