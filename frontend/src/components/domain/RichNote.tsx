import { Fragment } from "react"
import { Link } from "react-router-dom"
import type { AiNote } from "@/lib/api"

/**
 * Render AI prose with structure: tickers → stock links, [kap:ID] → KAP disclosure, [n:ID] → headline link,
 * signed numbers coloured. Paragraphs split on blank lines. Pure presentation — the text itself is untouched.
 */
export function RichNote({ note, className }: { note: AiNote; className?: string }) {
  const symbols = new Set(note.symbols ?? [])
  const heads = new Map((note.headlines ?? []).map((h) => [h.id, h]))
  const paragraphs = note.content.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean)
  return (
    <div className={className}>
      {paragraphs.map((p, i) => (
        <p key={i} className="mb-2.5 text-sm leading-relaxed last:mb-0">{renderInline(p, symbols, heads, note.kap_base)}</p>
      ))}
    </div>
  )
}

const TOKEN = /\[kap:(\d+)\]|\[n:(\d+)\]|\b([A-Z][A-Z0-9]{2,5})\b|([+\-−]?(?:%\s?)?\d[\d.,]*\s?(?:%|mn|bn|M|B|K|milyon|milyar|TL|USD|\$)?(?:\s?(?:TL|USD|\$|%))?)/g

function renderInline(text: string, symbols: Set<string>, heads: Map<number, { title: string; url: string | null; source: string }>, kapBase: string) {
  const out: React.ReactNode[] = []
  let last = 0
  let k = 0
  for (const m of text.matchAll(TOKEN)) {
    const idx = m.index ?? 0
    if (idx > last) out.push(text.slice(last, idx))
    const [raw, kap, n, sym, num] = m
    if (kap) {
      out.push(<a key={k++} href={`${kapBase}${kap}`} target="_blank" rel="noreferrer" className="rounded-sm border border-border px-1 font-mono text-[10px] text-muted-foreground hover:text-foreground" title="KAP">KAP {kap}</a>)
    } else if (n) {
      const h = heads.get(Number(n))
      out.push(h?.url
        ? <a key={k++} href={h.url} target="_blank" rel="noreferrer" title={`${h.source}: ${h.title}`} className="rounded-sm border border-primary/40 bg-primary/10 px-1 font-mono text-[10px] text-primary">{h.source || `n${n}`}</a>
        : <span key={k++} className="rounded-sm border border-border px-1 font-mono text-[10px] text-muted-foreground">n{n}</span>)
    } else if (sym) {
      out.push(symbols.has(sym) ? <Link key={k++} to={`/stocks/${sym}`} className="font-semibold text-foreground underline decoration-primary/50 underline-offset-2 hover:decoration-primary">{sym}</Link> : raw)
    } else if (num) {
      const neg = /^[-−]/.test(num.trim())
      const pos = /^\+/.test(num.trim())
      out.push(neg || pos ? <span key={k++} className={`num font-medium ${neg ? "text-negative" : "text-positive"}`}>{num}</span> : raw)
    } else out.push(raw)
    last = idx + raw.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out.map((x, i) => <Fragment key={i}>{x}</Fragment>)
}
