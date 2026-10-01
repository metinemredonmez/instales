import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import SwaggerUI from "swagger-ui-react"
import "swagger-ui-react/swagger-ui.css"
import "./swagger.css"
import { api } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { apiOrigin, guardSwaggerRequest } from "./swagger"

export default function SwaggerPanel() {
  const { user, token } = useAuth()
  const { t } = useI18n()
  const schema = useQuery({ queryKey: ["openapi", user?.id], queryFn: api.openApi, retry: false })
  const spec = useMemo(() => schema.data ? { ...schema.data, servers: [{ url: apiOrigin() }] } : undefined, [schema.data])
  if (schema.error) return <p role="alert">{schema.error.message}</p>
  if (!spec) return <p>{t("integrations.loading")}</p>
  // Disable the remote validator: neither the schema nor credentials leave the configured API origin.
  const options = { validatorUrl: null, queryConfigEnabled: false, persistAuthorization: false }
  return <div className="instilens-swagger rounded-lg bg-white p-2 text-slate-900 sm:p-4">
    <SwaggerUI key={token} spec={spec} {...options} docExpansion="none" defaultModelsExpandDepth={-1}
      filter displayRequestDuration requestInterceptor={(request) => { guardSwaggerRequest({ url: String(request.url) }); return request }}
      onComplete={(system) => { if (token) system.preauthorizeApiKey("BearerAuth", token) }} />
  </div>
}
