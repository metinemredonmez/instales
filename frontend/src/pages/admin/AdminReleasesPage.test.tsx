import { render, screen, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { DesktopFile, DesktopRelease } from "@/lib/api"

type Releases = { platforms: { key: string; label: string }[]; updater_pubkey_set: boolean; upload_key_set: boolean; releases: DesktopRelease[] }
const adminReleases = vi.fn<() => Promise<Releases>>()
vi.mock("@/lib/api", () => ({ api: { adminReleases: () => adminReleases(), adminPatchRelease: vi.fn(), adminDeleteRelease: vi.fn() } }))

import { AdminReleasesPage } from "./AdminReleasesPage"

const PLATFORMS = [
  { key: "darwin-aarch64", label: "macOS · Apple Silicon" },
  { key: "darwin-x86_64", label: "macOS · Intel" },
  { key: "windows-x86_64", label: "Windows" },
  { key: "linux-x86_64", label: "Linux" },
]
const file = (id: number, platform: string, kind: DesktopFile["kind"], filename: string, extra: Partial<DesktopFile> = {}): DesktopFile => ({
  id, platform, label: PLATFORMS.find((p) => p.key === platform)!.label, kind, filename, size: 42_000_000, sha256: "0".repeat(64),
  signed: false, downloads: 0, downloadable: kind === "INSTALLER", url: `https://app.example/api/v1/public/desktop/download/${id}/${filename}`, ...extra,
})
/** Linux built as rpm + deb + AppImage, an Apple Silicon dmg with an unsigned update bundle, nothing for Intel or Windows. */
const RELEASE: DesktopRelease = {
  id: 7, version: "0.3.0", status: "DRAFT", notes: "", created_by: "emre", created_at: "2026-09-16T10:00:00Z", published_at: null,
  files: [
    file(1, "darwin-aarch64", "INSTALLER", "InstiLens_0.3.0_aarch64.dmg", { downloads: 12 }),
    file(2, "darwin-aarch64", "UPDATE", "InstiLens_aarch64.app.tar.gz"),
    file(3, "linux-x86_64", "INSTALLER", "InstiLens_0.3.0_amd64.deb", { downloads: 3 }),
    file(4, "linux-x86_64", "INSTALLER", "InstiLens-0.3.0-1.x86_64.rpm", { size: 39_500_000 }),
    file(5, "linux-x86_64", "UPDATE", "InstiLens_0.3.0_amd64.AppImage", { signed: true, size: 88_100_000 }),
  ],
}

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><AdminReleasesPage /></MemoryRouter>
    </QueryClientProvider>,
  )
}

const card = (label: string) => within(screen.getByText(label).parentElement!)

describe("AdminReleasesPage", () => {
  beforeEach(() => { adminReleases.mockReset(); adminReleases.mockResolvedValue({ platforms: PLATFORMS, updater_pubkey_set: true, upload_key_set: true, releases: [RELEASE] }) })

  it("lists every artifact of a platform: both Linux installers and the signed AppImage", async () => {
    setup()
    expect(await screen.findByText("v0.3.0")).toBeInTheDocument()
    const linux = card("Linux")
    expect(linux.getByRole("link", { name: "InstiLens_0.3.0_amd64.deb" })).toHaveAttribute("href", "https://app.example/api/v1/public/desktop/download/3/InstiLens_0.3.0_amd64.deb")
    expect(linux.getByRole("link", { name: "InstiLens-0.3.0-1.x86_64.rpm" })).toHaveAttribute("href", "https://app.example/api/v1/public/desktop/download/4/InstiLens-0.3.0-1.x86_64.rpm")
    expect(linux.getByRole("link", { name: "InstiLens_0.3.0_amd64.AppImage" })).toBeInTheDocument()
    expect(linux.getAllByRole("listitem")).toHaveLength(3)
    expect(linux.getAllByText("kurulum")).toHaveLength(2)
    expect(linux.getByText("güncelleme")).toBeInTheDocument()
    expect(linux.getByText("✓ imzalı")).toBeInTheDocument()
    expect(linux.getByText("42.0 MB")).toBeInTheDocument()
    expect(linux.getByText("39.5 MB")).toBeInTheDocument()
    expect(linux.getByText("indirme: 3")).toBeInTheDocument()
  })

  it("marks an unsigned update bundle and says when a platform has no package", async () => {
    setup()
    await screen.findByText("v0.3.0")
    const mac = card("macOS · Apple Silicon")
    expect(mac.getAllByRole("listitem")).toHaveLength(2)
    expect(mac.getByText("— imza yok")).toBeInTheDocument()
    expect(mac.getByText("indirme: 12")).toBeInTheDocument()
    expect(card("Windows").getByText("paket yok")).toBeInTheDocument()
    expect(card("macOS · Intel").getByText("paket yok")).toBeInTheDocument()
  })

  it("links to the download page users see and keeps publish for a draft with an installer", async () => {
    setup()
    await screen.findByText("v0.3.0")
    expect(screen.getByRole("link", { name: "Kullanıcının gördüğü sayfa →" })).toHaveAttribute("href", "/desktop")
    expect(screen.getByRole("button", { name: "Yayımla" })).toBeEnabled()
  })
})
