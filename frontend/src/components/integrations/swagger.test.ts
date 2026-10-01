import { describe, expect, it } from "vitest"
import { guardSwaggerRequest } from "./swagger"

describe("Swagger credential boundary", () => {
  it("allows our API and health only", () => {
    for (const url of ["/api/v1/auth/me", "https://app.example.com/api/v1/radar?market=TR", "/health"]) {
      expect(guardSwaggerRequest({ url }, "https://app.example.com").url).toBe(url)
    }
  })
  it.each(["https://evil.example/api/v1/radar", "//evil.example/api/v1/radar", "/api/v1/../../outside", "/outside", "https://user:pass@app.example.com/api/v1/radar"])("blocks %s before sending credentials", url => {
    expect(() => guardSwaggerRequest({ url }, "https://app.example.com")).toThrow()
  })
})
