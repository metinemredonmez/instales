import { describe, expect, it } from "vitest"
import { timelineDetail, timelineTitle } from "./Timeline"
import { translate } from "@/lib/i18n"
import type { TimelineItem } from "@/lib/api"

const t = (k: Parameters<typeof translate>[1], v?: Parameters<typeof translate>[2]) => translate("en", k, v)
const item = (kind: TimelineItem["kind"], title: string, detail: string | null = null): TimelineItem => ({ date: "2026-09-01", kind, title, detail, confidence: null })

describe("timeline wording", () => {
  it("rewrites server event titles into neutral, translated ones", () => {
    expect(timelineTitle(t, item("EVENT", "İş Portföy: alım +12,500"))).toBe("İş Portföy: position increase +12.5K")
    expect(timelineTitle(t, item("EVENT", "Ak Portföy: satım -3,000"))).toBe("Ak Portföy: position decrease -3.0K")
  })
  it("translates period rows and passes unknown text through", () => {
    expect(timelineTitle(t, item("PERIOD", "4 fon artırdı · 1 azalttı"))).toBe("4 funds increased · 1 reduced")
    expect(timelineDetail(t, item("PERIOD", "x", "2 yeni · 0 çıkış · net +1,200 lot"))).toBe("2 new · 0 exited · net +1.2K lots")
    expect(timelineDetail(t, item("SIGNAL", "ACCUMULATION", "güç 71"))).toBe("strength 71")
    expect(timelineTitle(t, item("EVENT", "something else entirely"))).toBe("something else entirely")
  })
})
