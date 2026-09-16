import { Link } from "react-router-dom"
import { useI18n } from "@/lib/i18n"

/** Catch-all route inside the app shell. */
export function NotFoundPage() {
  const { t } = useI18n()
  return (
    <div className="mx-auto max-w-md rounded-lg border border-dashed p-10 text-center">
      <div className="num text-4xl font-semibold tracking-tight text-muted-foreground">404</div>
      <h1 className="mt-2 text-lg font-semibold">{t("notfound.title")}</h1>
      <p className="mt-1 text-sm text-muted-foreground">{t("notfound.body")}</p>
      <Link to="/" className="mt-4 inline-block text-sm text-primary hover:underline">{t("notfound.home")} →</Link>
    </div>
  )
}
