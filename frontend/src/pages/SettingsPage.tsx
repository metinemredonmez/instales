import { useI18n } from "@/lib/i18n"
import { NotifySettingsCard } from "@/components/domain/NotifySettingsCard"
import { AccountSecurityCard } from "@/components/domain/AccountSecurityCard"

/** Personal settings: delivery channels + account security. Alert rules stay on /alerts. */
export function SettingsPage() {
  const { t } = useI18n()
  return (
    <div className="space-y-5">
      <div><h1 className="text-2xl font-semibold tracking-tight">{t("settings.page.title")}</h1><p className="text-sm text-muted-foreground">{t("settings.page.sub")}</p></div>
      <div className="grid gap-5 lg:grid-cols-2">
        <NotifySettingsCard />
        <AccountSecurityCard />
      </div>
    </div>
  )
}
