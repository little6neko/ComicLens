import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import type { TranslationBatchActionResult } from "@/domain/api";
import { api } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";

import type { PretranslationBatchAction } from "./pretranslation-batch-card";

const successMessages: Record<PretranslationBatchAction, string> = {
  pause: "批次将在当前章完成后暂停",
  resume: "批次已继续",
  "cancel-pending": "已取消剩余章节，当前章会正常完成",
  "retry-failed": "失败章节已重新加入批量队列",
  close: "批次已结束",
};

export function usePretranslationBatchActions() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ batchId, action }: { batchId: string; action: PretranslationBatchAction }) =>
      runAction(batchId, action),
    onSuccess: async (result, variables) => {
      queryClient.setQueryData(
        queryKeys.translationOverview(result.batch.comicId),
        (current: unknown) =>
          current && typeof current === "object" ? { ...current, batch: result.batch } : current,
      );
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.translationOverview(result.batch.comicId),
        }),
        queryClient.invalidateQueries({ queryKey: queryKeys.backgroundTranslationBatches }),
        queryClient.invalidateQueries({ queryKey: queryKeys.backgroundTranslations }),
        result.batch.currentItem
          ? queryClient.invalidateQueries({
              queryKey: queryKeys.translation(
                result.batch.comicId,
                result.batch.currentItem.chapterId,
              ),
            })
          : Promise.resolve(),
      ]);
      toast.success(successMessages[variables.action]);
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "批次操作失败");
    },
  });
}

function runAction(batchId: string, action: PretranslationBatchAction) {
  const actions: Record<
    PretranslationBatchAction,
    (targetBatchId: string) => Promise<TranslationBatchActionResult>
  > = {
    pause: api.pauseTranslationBatch,
    resume: api.resumeTranslationBatch,
    "cancel-pending": api.cancelPendingTranslationBatch,
    "retry-failed": api.retryFailedTranslationBatch,
    close: api.closeTranslationBatch,
  };
  return actions[action](batchId);
}
