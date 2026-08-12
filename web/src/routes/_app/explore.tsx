import { createFileRoute } from "@tanstack/react-router";
import { CompassIcon } from "lucide-react";

import { PagePlaceholder } from "@/components/page-placeholder";

export const Route = createFileRoute("/_app/explore")({
  component: () => (
    <PagePlaceholder icon={CompassIcon} title="探索" description="搜索、分类与 Manga18fx 周榜。" />
  ),
});
