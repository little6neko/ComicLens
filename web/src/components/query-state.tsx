import { AlertCircleIcon, LoaderCircleIcon, RotateCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api-client";

export function LoadingState({ label = "正在读取 Comic…" }: { label?: string }) {
  return (
    <div className="flex min-h-48 items-center justify-center gap-3 text-sm text-muted-foreground">
      <LoaderCircleIcon className="size-5 animate-spin" />
      {label}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const message = error instanceof Error ? error.message : "请求失败，请稍后重试";
  const code = error instanceof ApiError ? error.code : "REQUEST_FAILED";
  return (
    <div className="rounded-3xl border border-destructive/25 bg-destructive/5 p-6">
      <div className="flex items-start gap-3">
        <AlertCircleIcon className="mt-0.5 size-5 shrink-0 text-destructive" />
        <div className="min-w-0 flex-1">
          <p className="font-medium">{message}</p>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{code}</p>
        </div>
        {retry && (
          <Button variant="outline" size="sm" onClick={retry}>
            <RotateCwIcon className="size-3.5" />
            重试
          </Button>
        )}
      </div>
    </div>
  );
}

export function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <div className="rounded-3xl border border-dashed p-10 text-center">
      <p className="font-medium">{title}</p>
      <p className="mt-2 text-sm text-muted-foreground">{description}</p>
    </div>
  );
}
