import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { beforeEach, expect, it, vi } from "vitest"
import { LangProvider } from "@/lib/i18n"

const mocks = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), send: vi.fn(), history: vi.fn() }))
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 3 }, token: "test" }) }))
vi.mock("@/components/integrations/SwaggerPanel", () => ({ default: () => <p>Swagger loaded</p> }))
vi.mock("@/lib/api", () => ({ api: { webhookEndpoints: mocks.list, createWebhook: mocks.create, sendWebhook: mocks.send, webhookMessages: mocks.history } }))
import IntegrationsPage from "./IntegrationsPage"
const ep = { id: "integration-1", name: "Example", enabled: true, target_url: "https://example.com/hook", incoming_path: "/api/v1/webhooks/incoming/integration-1" }

beforeEach(() => {
  vi.resetAllMocks()
  localStorage.setItem("instilens.lang", "en")
  mocks.list.mockResolvedValue([ep])
  mocks.history.mockResolvedValue([])
  mocks.send.mockResolvedValue({ status: "pending" })
})
function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><LangProvider><IntegrationsPage /></LangProvider></QueryClientProvider>)
  return { user: userEvent.setup(), qc }
}
it("opens Swagger and queues explicit events without claiming delivery", async () => {
  const { user } = setup()
  expect(await screen.findByText("Swagger loaded")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Webhook connections" }))
  const composer = await screen.findByLabelText("Event to send (JSON)")
  const original = JSON.parse((composer as HTMLTextAreaElement).value)
  await user.click(screen.getByRole("button", { name: "Queue event" }))
  expect(await screen.findByRole("status")).toHaveTextContent("Event queued")
  expect(mocks.send).toHaveBeenCalledWith(ep.id, original)
  expect(JSON.parse((composer as HTMLTextAreaElement).value).id).not.toBe(original.id)
})
it("rejects malformed envelopes locally and keeps failed sends retryable with the same id", async () => {
  const { user } = setup()
  await user.click(screen.getByRole("button", { name: "Webhook connections" }))
  const composer = await screen.findByLabelText("Event to send (JSON)")
  await user.clear(composer)
  await user.paste('{"id":"same-id","type":"custom.event","data":[]}')
  await user.click(screen.getByRole("button", { name: "Queue event" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("Enter valid JSON")
  expect(mocks.send).not.toHaveBeenCalled()
  await user.clear(composer)
  await user.paste('{"id":"same-id","type":"custom.event","data":{}}')
  mocks.send.mockRejectedValue(new Error("offline"))
  await user.click(screen.getByRole("button", { name: "Queue event" }))
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("offline"))
  expect((composer as HTMLTextAreaElement).value).toContain("same-id")
})
it("shows a new secret once without storing it in the query cache", async () => {
  const { user, qc } = setup()
  await user.click(screen.getByRole("button", { name: "Webhook connections" }))
  mocks.create.mockResolvedValue({ ...ep, signing_secret: "one-time-secret" })
  await user.type(screen.getByLabelText("Connection name"), "New")
  await user.click(screen.getByRole("button", { name: "Create" }))
  expect(await screen.findByText("one-time-secret")).toBeInTheDocument()
  expect(JSON.stringify(qc.getQueryCache().getAll().map(q => q.state.data))).not.toContain("one-time-secret")
  await user.click(screen.getByRole("button", { name: "Hide secret" }))
  expect(screen.queryByText("one-time-secret")).not.toBeInTheDocument()
})
