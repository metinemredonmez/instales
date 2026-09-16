import { describe, expect, it } from "vitest"
import { initials } from "./prefs"
import { setLocale } from "@/lib/format"

describe("initials", () => {
  it("takes the first letters of the first and last name, two letters of a single name, or the e-mail's first letter", () => {
    setLocale("en")
    expect(initials("Emre Dönmez", "e@x.io")).toBe("ED")
    expect(initials("Ada Byron Lovelace", "e@x.io")).toBe("AL")
    expect(initials("emre", "e@x.io")).toBe("EM")
    expect(initials(undefined, "emre@x.io")).toBe("E")
    expect(initials("  ", undefined)).toBe("?")
  })

  it("upper-cases in the UI locale — dotted İ only in Turkish", () => {
    setLocale("en")
    expect(initials("ian smith", undefined)).toBe("IS")
    setLocale("tr")
    expect(initials("ilhan irem", undefined)).toBe("İİ")
    setLocale("en")
  })
})
