import { useI18n } from "@/lib/i18n"
import { NewsRulesAdmin } from "@/components/domain/NewsRulesAdmin"

export function AdminNewsPage() {
  const { t } = useI18n()
  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("admin.news.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("admin.news.sub")}</p>
      </div>
      <NewsRulesAdmin />
    </>
  )
}
