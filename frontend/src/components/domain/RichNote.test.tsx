import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it } from "vitest"
import { RichNote } from "./RichNote"
import type { AiNote } from "@/lib/api"

const note = (content: string, extra: Partial<AiNote> = {}): AiNote => ({
  id: 1, kind: "stock", subject: "ASELS", as_of: "2026-09-01", lang: "tr", content,
  headline: "", highlights: [], watch: [], headline_ids: [], confidence_note: "",
  symbols: ["ASELS", "THYAO"], headlines: [{ id: 7, title: "Başlık", source: "AA", url: "https://example.test/7" }], kap_base: "https://www.kap.org.tr/tr/Bildirim/",
  model: "test", created_at: "2026-09-01T00:00:00Z", ...extra,
})

const renderNote = (n: AiNote) => render(<MemoryRouter><RichNote note={n} /></MemoryRouter>)

describe("RichNote", () => {
  it("links tickers that belong to the note and leaves other capitals alone", () => {
    renderNote(note("ASELS ve THYAO artırıldı; KAP verisi. TSLA yok."))
    expect(screen.getByRole("link", { name: "ASELS" })).toHaveAttribute("href", "/stocks/ASELS")
    expect(screen.getByRole("link", { name: "THYAO" })).toHaveAttribute("href", "/stocks/THYAO")
    expect(screen.queryByRole("link", { name: "TSLA" })).toBeNull()
    expect(screen.queryByRole("link", { name: "KAP" })).toBeNull()
  })

  it("turns [kap:ID] into a disclosure link and [n:ID] into a headline link", () => {
    renderNote(note("Kaynak [kap:1234567] ve [n:7]; bilinmeyen [n:99]."))
    expect(screen.getByRole("link", { name: "KAP 1234567" })).toHaveAttribute("href", "https://www.kap.org.tr/tr/Bildirim/1234567")
    const head = screen.getByRole("link", { name: "AA" })
    expect(head).toHaveAttribute("href", "https://example.test/7")
    expect(head).toHaveAttribute("target", "_blank")
    expect(screen.getByText("n99")).toBeInTheDocument()
  })

  it("colours signed numbers and leaves unsigned ones as plain text", () => {
    const { container } = renderNote(note("Net +12,5 mn TL giriş, fiyat -3,2%; toplam 1.200 lot."))
    const pos = container.querySelector(".text-positive")
    const neg = container.querySelector(".text-negative")
    expect(pos?.textContent).toBe("+12,5 mn TL")
    expect(neg?.textContent).toBe("-3,2%")
    expect(container.querySelectorAll(".text-positive, .text-negative")).toHaveLength(2)
    expect(container.textContent).toContain("toplam 1.200 lot")
  })

  it("splits paragraphs on blank lines", () => {
    const { container } = renderNote(note("İlk paragraf.\n\nİkinci paragraf.\n\n\n"))
    expect(container.querySelectorAll("p")).toHaveLength(2)
  })
})
