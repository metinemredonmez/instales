/** Constrain Swagger's executor to our API; never forward session credentials to another server. */
export function apiOrigin(): string {
  return new URL(import.meta.env.VITE_API_BASE || window.location.origin).origin
}
export function guardSwaggerRequest<T extends { url: string }>(request: T, origin = apiOrigin()): T {
  const url = new URL(request.url, origin)
  if (url.origin !== origin || url.username || url.password || !(url.pathname.startsWith("/api/v1/") || url.pathname === "/health")) {
    throw new Error("Swagger requests must target the InstiLens API")
  }
  return request
}
