import { createFileRoute } from "@tanstack/react-router";
import { HeartIcon } from "lucide-react";

import { PagePlaceholder } from "@/components/page-placeholder";

export const Route = createFileRoute("/_app/favorites")({
  component: () => (
    <PagePlaceholder icon={HeartIcon} title="收藏" description="保存在服务器上的 Comic 收藏。" />
  ),
});
