import { createFileRoute } from "@tanstack/react-router";
import { SettingsIcon } from "lucide-react";

import { PagePlaceholder } from "@/components/page-placeholder";

export const Route = createFileRoute("/_app/settings")({
  component: () => (
    <PagePlaceholder icon={SettingsIcon} title="设置" description="阅读、翻译、接口与缓存设置。" />
  ),
});
