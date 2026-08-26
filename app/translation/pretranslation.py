from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable, Sequence
from typing import Protocol

from app.domain.pretranslation import (
    TranslationBatchSummary,
    TranslationBatchTaskSummary,
)
from app.domain.translation import TranslationTaskProgress, TranslationTaskState
from app.errors import AppError
from app.observability import log_event, short_ref
from app.repositories.pretranslation import PretranslationRepository

logger = logging.getLogger(__name__)

ACTIVE_TASK_STATUSES = {
    "preparing",
    "queued",
    "running",
    "stopping_after_page",
    "stopping_after_segment",
}
CONFIG_ERROR_CODES = {
    "OCR_NOT_CONFIGURED",
    "OCR_AUTH_NOT_CONFIGURED",
    "OCR_AUTH_INVALID",
    "TRANSLATOR_NOT_CONFIGURED",
    "TRANSLATOR_INVALID",
}


class TranslationManagerProtocol(Protocol):
    def add_activity_listener(self, listener: Callable[[], None]) -> Callable[[], None]: ...

    def validate_runtime_services(self) -> None: ...

    def has_interactive_tasks(self) -> bool: ...

    def state(self, comic_id: str, chapter_id: str) -> TranslationTaskState: ...

    def state_for_generation(self, generation_id: str) -> TranslationTaskState | None: ...

    def progress_for_generation(self, generation_id: str) -> TranslationTaskProgress | None: ...

    async def start(
        self,
        comic_id: str,
        chapter_id: str,
        *,
        batch_item_id: str | None = None,
    ) -> TranslationTaskState: ...

    async def pause_generation(
        self,
        generation_id: str,
    ) -> TranslationTaskState | None: ...

    async def retry_failed(
        self,
        comic_id: str,
        chapter_id: str,
        *,
        batch_item_id: str | None = None,
    ) -> tuple[TranslationTaskState, int]: ...


class PretranslationCoordinator:
    def __init__(
        self,
        *,
        repository: PretranslationRepository,
        manager: TranslationManagerProtocol,
        poll_interval: float = 0.25,
    ) -> None:
        self.repository = repository
        self.manager = manager
        self.poll_interval = poll_interval
        self._wake_event = asyncio.Event()
        self._scheduler_task: asyncio.Task[None] | None = None
        self._remove_activity_listener: Callable[[], None] | None = None
        self._shutting_down = False

    def start(self) -> None:
        if self._scheduler_task is not None and not self._scheduler_task.done():
            return
        self._shutting_down = False
        if self._remove_activity_listener is None:
            self._remove_activity_listener = self.manager.add_activity_listener(self.wake)
        self._scheduler_task = asyncio.create_task(
            self._run_scheduler(),
            name="pretranslation-coordinator",
        )
        self.wake()

    async def shutdown(self) -> None:
        self._shutting_down = True
        if self._remove_activity_listener is not None:
            self._remove_activity_listener()
            self._remove_activity_listener = None
        task = self._scheduler_task
        self._scheduler_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def wake(self) -> None:
        if not self._shutting_down:
            self._wake_event.set()

    def create_batch(
        self,
        comic_id: str,
        comic_title: str,
        chapters: Sequence[tuple[str, str]],
    ) -> TranslationBatchSummary:
        if self.repository.open_batch_for_comic(comic_id) is not None:
            raise AppError(
                "TRANSLATION_BATCH_EXISTS",
                "该漫画已有未结束的预先翻译批次",
                409,
                False,
            )
        self.manager.validate_runtime_services()
        try:
            batch_id = self.repository.create_batch(comic_id, comic_title, chapters)
        except sqlite3.IntegrityError as exc:
            raise AppError(
                "TRANSLATION_BATCH_EXISTS",
                "该漫画已有未结束的预先翻译批次",
                409,
                False,
            ) from exc
        self._log("created", batch_id, comic=comic_id, chapters=len(chapters))
        self.wake()
        return self._summary_or_error(batch_id)

    def pause(self, batch_id: str) -> TranslationBatchSummary:
        row = self._batch_or_error(batch_id)
        status = str(row["status"])
        if status in {"pausing", "paused"}:
            return self._summary_or_error(batch_id)
        if status not in {"queued", "running", "pausing", "paused"}:
            raise AppError(
                "TRANSLATION_BATCH_NOT_PAUSABLE",
                "当前批次状态无法暂停",
                409,
                False,
            )
        self.repository.request_pause(batch_id, reason="user")
        self._log("pause_requested", batch_id, comic=str(row["comic_id"]))
        self.wake()
        return self._summary_or_error(batch_id)

    def resume(self, batch_id: str) -> TranslationBatchSummary:
        row = self._batch_or_error(batch_id)
        status = str(row["status"])
        if status in {"queued", "running"}:
            return self._summary_or_error(batch_id)
        if status not in {"paused", "failed"}:
            raise AppError(
                "TRANSLATION_BATCH_NOT_RESUMABLE",
                "当前批次状态无法继续",
                409,
                False,
            )
        self.manager.validate_runtime_services()
        self.repository.resume_batch(batch_id)
        self._log("resumed", batch_id, comic=str(row["comic_id"]))
        self.wake()
        return self._summary_or_error(batch_id)

    def cancel_pending(self, batch_id: str) -> TranslationBatchSummary:
        row = self._batch_or_error(batch_id)
        status = str(row["status"])
        if status == "cancelled":
            return self._summary_or_error(batch_id)
        if status not in {"queued", "running", "pausing", "paused", "cancelling"}:
            raise AppError(
                "TRANSLATION_BATCH_NOT_CANCELLABLE",
                "当前批次没有可取消的剩余章节",
                409,
                False,
            )
        self.repository.cancel_pending(batch_id)
        self._log("cancel_pending", batch_id, comic=str(row["comic_id"]))
        self.wake()
        return self._summary_or_error(batch_id)

    def retry_failed(self, batch_id: str) -> TranslationBatchSummary:
        row = self._batch_or_error(batch_id)
        status = str(row["status"])
        if status in {"queued", "running"}:
            return self._summary_or_error(batch_id)
        if status != "completed_with_errors":
            raise AppError(
                "TRANSLATION_BATCH_HAS_NO_FAILED_CHAPTERS",
                "当前批次没有可重试的失败章节",
                409,
                False,
            )
        self.manager.validate_runtime_services()
        _updated, count = self.repository.retry_failed(batch_id)
        self._log(
            "failed_requeued",
            batch_id,
            comic=str(row["comic_id"]),
            chapters=count,
        )
        self.wake()
        return self._summary_or_error(batch_id)

    def close(self, batch_id: str) -> TranslationBatchSummary:
        row = self._batch_or_error(batch_id)
        status = str(row["status"])
        if status == "cancelled":
            return self._summary_or_error(batch_id)
        if status not in {"completed_with_errors", "failed"}:
            raise AppError(
                "TRANSLATION_BATCH_NOT_CLOSABLE",
                "当前批次仍在执行，请先取消剩余章节",
                409,
                False,
            )
        self.repository.close_failed_batch(batch_id)
        self._log("closed", batch_id, comic=str(row["comic_id"]))
        self.wake()
        return self._summary_or_error(batch_id)

    def summary(self, batch_id: str) -> TranslationBatchSummary | None:
        current = self.repository.current_item(batch_id)
        current_task = None
        if current is not None:
            generation = self.repository.owned_generation(str(current["batch_item_id"]))
            if generation is not None:
                progress = self.manager.progress_for_generation(str(generation["generation_id"]))
                if progress is not None:
                    current_task = TranslationBatchTaskSummary(**progress.model_dump())
        return self.repository.summary(batch_id, current_task=current_task)

    def background_batches(self) -> list[TranslationBatchSummary]:
        summaries = [self.summary(batch_id) for batch_id in self.repository.background_batch_ids()]
        return [summary for summary in summaries if summary is not None]

    async def _run_scheduler(self) -> None:
        while True:
            try:
                progressed = await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Pretranslation coordinator tick failed")
                self._fail_active_batch()
                progressed = False
            if progressed:
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass
            finally:
                self._wake_event.clear()

    async def _tick(self) -> bool:
        batch = self.repository.scheduler_batch()
        if self.manager.has_interactive_tasks():
            if batch is None:
                return False
            return await self._yield_to_interactive(batch)

        yielded_batch = self.repository.interactive_yielded_batch()
        if yielded_batch is not None:
            return await self._finish_interactive_yield(yielded_batch)

        if batch is None:
            return False
        batch_id = str(batch["batch_id"])

        status = str(batch["status"])
        if status == "queued":
            if self.repository.claim_batch(batch_id):
                self._log("started", batch_id, comic=str(batch["comic_id"]))
                return True
            return False

        current = self.repository.current_item(batch_id)
        if current is not None:
            return await self._reconcile_current(batch, current)

        if status in {"pausing", "cancelling"}:
            self.repository.settle_after_item(batch_id)
            self._log("settled", batch_id, comic=str(batch["comic_id"]), status=status)
            return True
        if status != "running":
            return False

        item = self.repository.next_pending_item(batch_id)
        if item is None:
            before = str(batch["status"])
            settled = self.repository.settle_after_item(batch_id)
            if settled is not None and str(settled["status"]) != before:
                self._log(
                    "completed",
                    batch_id,
                    comic=str(batch["comic_id"]),
                    status=str(settled["status"]),
                )
                return True
            return False
        item_id = str(item["batch_item_id"])
        if not self.repository.claim_item(item_id):
            return True
        self._log_item("item_started", batch, item)
        return await self._route_new_item(batch, self.repository.batch_item(item_id) or item)

    async def _yield_to_interactive(self, batch: sqlite3.Row) -> bool:
        current = self.repository.current_item(str(batch["batch_id"]))
        if current is None:
            return False
        generation = self.repository.owned_generation(str(current["batch_item_id"]))
        if generation is None:
            return False
        task = self.manager.state_for_generation(str(generation["generation_id"]))
        if task is None or task.status not in ACTIVE_TASK_STATUSES:
            return False
        if not bool(batch["interactive_yielded"]):
            await self.manager.pause_generation(str(generation["generation_id"]))
            self.repository.set_interactive_yielded(str(batch["batch_id"]), True)
            if str(batch["pause_reason"] or "") == "user":
                self.repository.request_pause(str(batch["batch_id"]), reason="user")
            self._log_item("interactive_yield", batch, current)
            return True
        return False

    async def _finish_interactive_yield(self, batch: sqlite3.Row) -> bool:
        batch_id = str(batch["batch_id"])
        current = self.repository.current_item(batch_id)
        if current is None:
            current = self.repository.owned_unfinished_item(batch_id)
        if current is None:
            self.repository.set_interactive_yielded(batch_id, False)
            self._log("interactive_cleared", batch_id, comic=str(batch["comic_id"]))
            return True
        generation = self.repository.owned_generation(str(current["batch_item_id"]))
        if generation is None:
            self.repository.set_interactive_yielded(batch_id, False)
            return True
        task = self.manager.state_for_generation(str(generation["generation_id"]))
        if task is not None and task.status in ACTIVE_TASK_STATUSES:
            return False
        if str(batch["status"]) == "paused" or str(batch["pause_reason"] or "") == "user":
            self.repository.set_interactive_yielded(batch_id, False)
            self._log_item("interactive_pause_kept", batch, current)
            return True
        if task is not None and task.status == "paused":
            self.repository.set_interactive_yielded(batch_id, False)
            self._log_item("interactive_resumed", batch, current)
            return await self._start_or_retry_item(batch, current, task)
        self.repository.set_interactive_yielded(batch_id, False)
        self._log_item("interactive_resumed", batch, current)
        return True

    async def _route_new_item(self, batch: sqlite3.Row, item: sqlite3.Row) -> bool:
        comic_id = str(batch["comic_id"])
        chapter_id = str(item["chapter_id"])
        state = self.manager.state(comic_id, chapter_id)
        if state.status == "completed" and not state.failed_pages and not state.failed_segments:
            self.repository.clear_resume_requested(str(batch["batch_id"]))
            self.repository.finish_item(str(item["batch_item_id"]), "skipped")
            self._log_item("item_skipped", batch, item)
            self.repository.settle_after_item(str(batch["batch_id"]))
            return True
        if state.status in ACTIVE_TASK_STATUSES:
            return False
        return await self._start_or_retry_item(batch, item, state)

    async def _start_or_retry_item(
        self,
        batch: sqlite3.Row,
        item: sqlite3.Row,
        state: TranslationTaskState,
    ) -> bool:
        batch_id = str(batch["batch_id"])
        item_id = str(item["batch_item_id"])
        comic_id = str(batch["comic_id"])
        chapter_id = str(item["chapter_id"])
        try:
            self.manager.validate_runtime_services()
        except AppError as exc:
            self.repository.pause_for_config(
                batch_id,
                item_id,
                error_code=exc.code,
                error_summary=exc.message,
            )
            self._log_item("config_paused", batch, item, error=exc.code)
            return True

        try:
            if state.status == "completed_with_errors":
                await self.manager.retry_failed(
                    comic_id,
                    chapter_id,
                    batch_item_id=item_id,
                )
                action = "retry_failed"
            else:
                await self.manager.start(
                    comic_id,
                    chapter_id,
                    batch_item_id=item_id,
                )
                action = "start"
        except AppError as exc:
            if exc.code in CONFIG_ERROR_CODES:
                self.repository.pause_for_config(
                    batch_id,
                    item_id,
                    error_code=exc.code,
                    error_summary=exc.message,
                )
                self._log_item("config_paused", batch, item, error=exc.code)
            else:
                self._finish_item_failed(batch, item, exc.code, exc.message)
            return True
        except Exception:
            logger.exception(
                "Failed to start pretranslation chapter",
                extra={"batch_ref": short_ref(batch_id), "chapter_id": chapter_id},
            )
            self._finish_item_failed(
                batch,
                item,
                "CHAPTER_START_FAILED",
                "章节翻译任务启动失败",
            )
            return True
        self.repository.clear_resume_requested(batch_id)
        self._log_item("item_dispatched", batch, item, action=action)
        return True

    async def _reconcile_current(self, batch: sqlite3.Row, item: sqlite3.Row) -> bool:
        generation = self.repository.owned_generation(str(item["batch_item_id"]))
        if generation is None:
            return await self._route_new_item(batch, item)
        task = self.manager.state_for_generation(str(generation["generation_id"]))
        if task is None:
            self.repository.set_batch_failed(
                str(batch["batch_id"]),
                error_code="BATCH_GENERATION_MISSING",
                error_summary="批次关联的翻译任务不存在",
            )
            return True
        if task.status in {"preparing", "queued"}:
            return await self._start_or_retry_item(batch, item, task)
        if task.status == "running":
            if bool(batch["resume_requested"]):
                self.repository.clear_resume_requested(str(batch["batch_id"]))
                return True
            return False
        if task.status in {"stopping_after_page", "stopping_after_segment"}:
            return False
        if task.status == "paused":
            if str(batch["status"]) == "paused":
                return False
            if bool(batch["resume_requested"]):
                return await self._start_or_retry_item(batch, item, task)
            settled = self.repository.settle_stopped_current(
                str(batch["batch_id"]),
                str(item["batch_item_id"]),
            )
            self._log_item(
                "current_stopped",
                batch,
                item,
                status=str(settled["status"]) if settled is not None else "missing",
            )
            return True
        if task.status == "completed":
            self.repository.clear_resume_requested(str(batch["batch_id"]))
            self.repository.finish_item(str(item["batch_item_id"]), "completed")
            self._log_item("item_completed", batch, item)
            self.repository.settle_after_item(str(batch["batch_id"]))
            return True
        if task.status in {"completed_with_errors", "failed"}:
            code, summary = self._task_error(task)
            self._finish_item_failed(batch, item, code, summary)
            return True
        return False

    def _finish_item_failed(
        self,
        batch: sqlite3.Row,
        item: sqlite3.Row,
        code: str,
        summary: str,
    ) -> None:
        self.repository.clear_resume_requested(str(batch["batch_id"]))
        self.repository.finish_item(
            str(item["batch_item_id"]),
            "failed",
            error_code=code,
            error_summary=summary,
        )
        self._log_item("item_failed", batch, item, error=code)
        self.repository.settle_after_item(str(batch["batch_id"]))

    @staticmethod
    def _task_error(task: TranslationTaskState) -> tuple[str, str]:
        for page in task.pages:
            if page.error is not None:
                return page.error.code, page.error.message
            for segment in page.segments:
                if segment.error is not None:
                    return segment.error.code, segment.error.message
        return "CHAPTER_FAILED", "本话翻译仍有失败项"

    def _fail_active_batch(self) -> None:
        try:
            batch = self.repository.scheduler_batch()
            if batch is None:
                return
            batch_id = str(batch["batch_id"])
            self.repository.set_batch_failed(
                batch_id,
                error_code="COORDINATOR_ERROR",
                error_summary="批次协调器执行失败",
            )
            self._log(
                "failed",
                batch_id,
                level=logging.ERROR,
                comic=str(batch["comic_id"]),
                error="COORDINATOR_ERROR",
            )
        except Exception:
            logger.exception("Failed to persist pretranslation coordinator error")

    def _batch_or_error(self, batch_id: str) -> sqlite3.Row:
        row = self.repository.batch(batch_id)
        if row is None:
            raise AppError(
                "TRANSLATION_BATCH_NOT_FOUND",
                "预先翻译批次不存在",
                404,
                False,
            )
        return row

    def _summary_or_error(self, batch_id: str) -> TranslationBatchSummary:
        summary = self.summary(batch_id)
        if summary is None:
            raise AppError(
                "TRANSLATION_BATCH_NOT_FOUND",
                "预先翻译批次不存在",
                404,
                False,
            )
        return summary

    @staticmethod
    def _log(
        event: str,
        batch_id: str,
        *,
        level: int = logging.INFO,
        **fields: object,
    ) -> None:
        log_event(
            "batch",
            event,
            level=level,
            batch_ref=short_ref(batch_id),
            **fields,
        )

    def _log_item(
        self,
        event: str,
        batch: sqlite3.Row,
        item: sqlite3.Row,
        **fields: object,
    ) -> None:
        self._log(
            event,
            str(batch["batch_id"]),
            comic=str(batch["comic_id"]),
            chapter=str(item["chapter_id"]),
            position=int(item["position"]),
            **fields,
        )
