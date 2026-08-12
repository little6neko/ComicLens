import { createFileRoute } from "@tanstack/react-router";
import { ScanSearchIcon } from "lucide-react";

import { PagePlaceholder } from "@/components/page-placeholder";

export const Route = createFileRoute("/_app/")({
  component: () => (
    <PagePlaceholder
      icon={ScanSearchIcon}
      title="ComicLens"
      description="浏览 Manga18fx 更新，并在阅读时逐张显示实时翻译结果。"
    />
  ),
});
