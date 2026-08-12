import { createFileRoute } from "@tanstack/react-router";
import { HistoryIcon } from "lucide-react";

import { PagePlaceholder } from "@/components/page-placeholder";

export const Route = createFileRoute("/_app/history")({
  component: () => (
    <PagePlaceholder icon={HistoryIcon} title="历史" description="继续上次阅读的章节和页码。" />
  ),
});
