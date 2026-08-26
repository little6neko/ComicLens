from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.domain.translation import TranslationTaskProgress, TranslationTaskState
from app.errors import AppError
from app.repositories.database import Database
from app.repositories.pretranslation import PretranslationRepository
from app.repositories.translation import TranslationRepository
from app.translation.pretranslation import PretranslationCoordinator


class FakeTranslationManager:
    def __init__(self, repository: TranslationRepository) -> None:
        self.repository = repository
        self.start_calls: list[tuple[str, str, str | None]] = []
        self.retry_calls: list[tuple[str, str, str | None]] = []
        self.pause_calls: list[tuple[str, str]] = []
        self.paused_generation_ids: list[str] = []
        self.listeners: set[Callable[[], None]] = set()
        self.interactive = False
        self.config_error: AppError | None = None
        self.max_owned_active = 0
        self.defer_pause = False

    def add_activity_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self.listeners.add(listener)

        def remove() -> None:
            self.listeners.discard(listener)

        return remove

    def validate_runtime_services(self) -> None:
        if self.config_error is not None:
            raise self.config_error

    def has_interactive_tasks(self) -> bool:
        return self.interactive

    def state(self, comic_id: str, chapter_id: str) -> TranslationTaskState:
        return self.repository.task_state(comic_id, chapter_id)

    def state_for_generation(self, generation_id: str) -> TranslationTaskState | None:
        generation = self.repository.generation(generation_id)
        if generation is None:
            return None
        return self.repository.task_state(
            str(generation["comic_id"]),
            str(generation["chapter_id"]),
            generation_id,
        )

    def progress_for_generation(self, generation_id: str) -> TranslationTaskProgress | None:
        return self.repository.task_progress(generation_id)

    async def start(
        self,
        comic_id: str,
        chapter_id: str,
        *,
        batch_item_id: str | None = None,
    ) -> TranslationTaskState:
        self.validate_runtime_services()
        self.start_calls.append((comic_id, chapter_id, batch_item_id))
        generation = self.repository.latest_generation(comic_id, chapter_id)
        if generation is None:
            generation_id = self.repository.create_generation(
                comic_id,
                chapter_id,
                semantic_fingerprint=f"fake:{comic_id}:{chapter_id}",
                semantic_settings={},
                page_indexes=[],
                kind="normal",
                batch_item_id=batch_item_id,
            )
        else:
            generation_id = str(generation["generation_id"])
            if batch_item_id is not None:
                self.repository.assign_batch_item(
                    generation_id,
                    batch_item_id,
                    comic_id,
                    chapter_id,
                )
        self.repository.set_generation_status(generation_id, "running")
        self._record_concurrency()
        self.notify()
        return self.repository.task_state(comic_id, chapter_id, generation_id)

    async def pause(self, comic_id: str, chapter_id: str) -> TranslationTaskState:
        self.pause_calls.append((comic_id, chapter_id))
        active = self.repository.active_generation(comic_id, chapter_id)
        if active is not None:
            self.repository.set_generation_status(
                str(active["generation_id"]),
                "stopping_after_segment" if self.defer_pause else "paused",
            )
        self.notify()
        return self.repository.task_state(comic_id, chapter_id)

    async def pause_generation(self, generation_id: str) -> TranslationTaskState | None:
        generation = self.repository.generation(generation_id)
        if generation is None:
            return None
        comic_id = str(generation["comic_id"])
        chapter_id = str(generation["chapter_id"])
        self.pause_calls.append((comic_id, chapter_id))
        self.paused_generation_ids.append(generation_id)
        self.repository.set_generation_status(
            generation_id,
            "stopping_after_segment" if self.defer_pause else "paused",
        )
        self.notify()
        return self.repository.task_state(comic_id, chapter_id, generation_id)

    async def retry_failed(
        self,
        comic_id: str,
        chapter_id: str,
        *,
        batch_item_id: str | None = None,
    ) -> tuple[TranslationTaskState, int]:
        self.validate_runtime_services()
        self.retry_calls.append((comic_id, chapter_id, batch_item_id))
        generation = self.repository.latest_generation(comic_id, chapter_id)
        assert generation is not None
        generation_id = str(generation["generation_id"])
        if batch_item_id is not None:
            self.repository.assign_batch_item(
                generation_id,
                batch_item_id,
                comic_id,
                chapter_id,
            )
        self.repository.set_generation_status(generation_id, "running")
        self._record_concurrency()
        self.notify()
        return self.repository.task_state(comic_id, chapter_id, generation_id), 1

    def seed(self, comic_id: str, chapter_id: str, status: str) -> str:
        generation_id = self.repository.create_generation(
            comic_id,
            chapter_id,
            semantic_fingerprint=f"seed:{comic_id}:{chapter_id}",
            semantic_settings={},
            page_indexes=[],
            kind="normal",
        )
        self.repository.set_generation_status(generation_id, status)
        return generation_id

    def complete(self, comic_id: str, chapter_id: str, status: str = "completed") -> None:
        generation = self.repository.latest_generation(comic_id, chapter_id)
        assert generation is not None
        self.repository.set_generation_status(str(generation["generation_id"]), status)
        self.notify()

    def set_interactive(self, active: bool) -> None:
        self.interactive = active
        self.notify()

    def finish_pause(self, comic_id: str, chapter_id: str) -> None:
        generation = self.repository.latest_generation(comic_id, chapter_id)
        assert generation is not None
        self.repository.set_generation_status(str(generation["generation_id"]), "paused")
        self.notify()

    def notify(self) -> None:
        for listener in tuple(self.listeners):
            listener()

    def _record_concurrency(self) -> None:
        active = int(
            self.repository.database.scalar(
                """
                SELECT COUNT(*) FROM translation_generations generations
                JOIN translation_batch_items items
                  ON items.batch_item_id = generations.batch_item_id
                JOIN translation_batches batches ON batches.batch_id = items.batch_id
                WHERE generations.status IN ('preparing', 'queued', 'running')
                  AND batches.status NOT IN ('completed', 'cancelled')
                """
            )
            or 0
        )
        self.max_owned_active = max(self.max_owned_active, active)


@dataclass(slots=True)
class CoordinatorHarness:
    database: Database
    batches: PretranslationRepository
    translations: TranslationRepository
    manager: FakeTranslationManager
    coordinator: PretranslationCoordinator

    async def close(self) -> None:
        await self.coordinator.shutdown()
        self.database.close()


def create_coordinator_harness(tmp_path: Path) -> CoordinatorHarness:
    database = Database(tmp_path / "coordinator.db")
    batches = PretranslationRepository(database)
    translations = TranslationRepository(database)
    manager = FakeTranslationManager(translations)
    coordinator = PretranslationCoordinator(
        repository=batches,
        manager=manager,
        poll_interval=0.01,
    )
    return CoordinatorHarness(database, batches, translations, manager, coordinator)


async def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    async def poll() -> None:
        while not predicate():  # noqa: ASYNC110 - persisted scheduler state is intentional
            await asyncio.sleep(0.005)

    await asyncio.wait_for(poll(), timeout)


@pytest.mark.asyncio
async def test_batches_run_old_to_new_with_one_global_slot(tmp_path: Path) -> None:
    harness = create_coordinator_harness(tmp_path)
    try:
        first = harness.coordinator.create_batch(
            "alpha",
            "Alpha",
            [("chapter-1", "Chapter 1"), ("chapter-2", "Chapter 2")],
        )
        second = harness.coordinator.create_batch(
            "beta",
            "Beta",
            [("chapter-1", "Chapter 1")],
        )
        harness.coordinator.start()

        await wait_for(lambda: len(harness.manager.start_calls) == 1)
        assert harness.manager.start_calls[0][:2] == ("alpha", "chapter-1")
        running = harness.coordinator.summary(first.batch_id)
        assert running is not None
        assert running.current_item is not None
        assert running.current_item.chapter_id == "chapter-1"
        assert running.current_task is not None
        assert running.current_task.stage == "processing"
        assert running.current_task.prepared_pages == 0
        harness.manager.complete("alpha", "chapter-1")
        await wait_for(lambda: len(harness.manager.start_calls) == 2)
        assert harness.manager.start_calls[1][:2] == ("alpha", "chapter-2")
        harness.manager.complete("alpha", "chapter-2")
        await wait_for(lambda: len(harness.manager.start_calls) == 3)
        assert harness.manager.start_calls[2][:2] == ("beta", "chapter-1")
        harness.manager.complete("beta", "chapter-1")

        await wait_for(
            lambda: harness.batches.batch(first.batch_id)["status"] == "completed"
            and harness.batches.batch(second.batch_id)["status"] == "completed"
        )
        assert harness.manager.max_owned_active == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_incremental_routes_skip_retry_and_resume_to_existing_manager_operations(
    tmp_path: Path,
) -> None:
    harness = create_coordinator_harness(tmp_path)
    try:
        harness.manager.seed("alpha", "chapter-skip", "completed")
        harness.manager.seed("alpha", "chapter-retry", "completed_with_errors")
        harness.manager.seed("alpha", "chapter-paused", "paused")
        batch = harness.coordinator.create_batch(
            "alpha",
            "Alpha",
            [
                ("chapter-skip", "Complete"),
                ("chapter-retry", "Needs retry"),
                ("chapter-paused", "Paused"),
            ],
        )
        harness.coordinator.start()

        await wait_for(lambda: len(harness.manager.retry_calls) == 1)
        items = harness.batches.batch_items(batch.batch_id)
        assert items[0]["status"] == "skipped"
        assert harness.manager.retry_calls[0][:2] == ("alpha", "chapter-retry")
        harness.manager.complete("alpha", "chapter-retry")
        await wait_for(lambda: len(harness.manager.start_calls) == 1)
        assert harness.manager.start_calls[0][:2] == ("alpha", "chapter-paused")
        harness.manager.complete("alpha", "chapter-paused")
        await wait_for(lambda: harness.batches.batch(batch.batch_id)["status"] == "completed")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_failed_chapter_continues_and_batch_retry_only_requeues_failure(
    tmp_path: Path,
) -> None:
    harness = create_coordinator_harness(tmp_path)
    try:
        batch = harness.coordinator.create_batch(
            "alpha",
            "Alpha",
            [("chapter-1", "Chapter 1"), ("chapter-2", "Chapter 2")],
        )
        harness.coordinator.start()
        await wait_for(lambda: len(harness.manager.start_calls) == 1)
        harness.manager.complete("alpha", "chapter-1", "failed")
        await wait_for(lambda: len(harness.manager.start_calls) == 2)
        harness.manager.complete("alpha", "chapter-2")
        await wait_for(
            lambda: harness.batches.batch(batch.batch_id)["status"]
            == "completed_with_errors"
        )

        summary = harness.coordinator.retry_failed(batch.batch_id)
        assert summary.status == "queued"
        items = harness.batches.batch_items(batch.batch_id)
        assert [str(item["status"]) for item in items] == ["pending", "completed"]
        await wait_for(lambda: len(harness.manager.start_calls) == 3)
        assert harness.manager.start_calls[2][:2] == ("alpha", "chapter-1")
        harness.manager.complete("alpha", "chapter-1")
        await wait_for(lambda: harness.batches.batch(batch.batch_id)["status"] == "completed")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_pause_waits_for_current_chapter_and_cancel_keeps_current_running(
    tmp_path: Path,
) -> None:
    harness = create_coordinator_harness(tmp_path)
    try:
        batch = harness.coordinator.create_batch(
            "alpha",
            "Alpha",
            [
                ("chapter-1", "Chapter 1"),
                ("chapter-2", "Chapter 2"),
                ("chapter-3", "Chapter 3"),
            ],
        )
        harness.coordinator.start()
        await wait_for(lambda: len(harness.manager.start_calls) == 1)
        paused = harness.coordinator.pause(batch.batch_id)
        assert paused.status == "pausing"
        assert harness.manager.pause_calls == []
        harness.manager.complete("alpha", "chapter-1")
        await wait_for(lambda: harness.batches.batch(batch.batch_id)["status"] == "paused")
        assert len(harness.manager.start_calls) == 1

        harness.coordinator.resume(batch.batch_id)
        await wait_for(lambda: len(harness.manager.start_calls) == 2)
        cancelling = harness.coordinator.cancel_pending(batch.batch_id)
        assert cancelling.status == "cancelling"
        assert harness.batches.batch_items(batch.batch_id)[2]["status"] == "cancelled"
        harness.manager.complete("alpha", "chapter-2")
        await wait_for(lambda: harness.batches.batch(batch.batch_id)["status"] == "cancelled")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_force_stopped_owned_chapter_pauses_batch_until_resumed(
    tmp_path: Path,
) -> None:
    harness = create_coordinator_harness(tmp_path)
    try:
        batch = harness.coordinator.create_batch(
            "alpha",
            "Alpha",
            [("chapter-1", "Chapter 1")],
        )
        harness.coordinator.start()
        await wait_for(lambda: len(harness.manager.start_calls) == 1)

        harness.manager.finish_pause("alpha", "chapter-1")
        await wait_for(lambda: harness.batches.batch(batch.batch_id)["status"] == "paused")
        await asyncio.sleep(0.05)
        assert len(harness.manager.start_calls) == 1
        assert harness.batches.batch_items(batch.batch_id)[0]["status"] == "running"

        harness.coordinator.resume(batch.batch_id)
        await wait_for(lambda: len(harness.manager.start_calls) == 2)
        harness.manager.complete("alpha", "chapter-1")
        await wait_for(lambda: harness.batches.batch(batch.batch_id)["status"] == "completed")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_force_stopped_chapter_finishes_batch_when_pending_items_were_cancelled(
    tmp_path: Path,
) -> None:
    harness = create_coordinator_harness(tmp_path)
    try:
        batch = harness.coordinator.create_batch(
            "alpha",
            "Alpha",
            [("chapter-1", "Chapter 1"), ("chapter-2", "Chapter 2")],
        )
        harness.coordinator.start()
        await wait_for(lambda: len(harness.manager.start_calls) == 1)
        assert harness.coordinator.cancel_pending(batch.batch_id).status == "cancelling"

        harness.manager.finish_pause("alpha", "chapter-1")
        await wait_for(
            lambda: harness.batches.batch(batch.batch_id)["status"] == "cancelled"
        )
        assert [
            str(item["status"]) for item in harness.batches.batch_items(batch.batch_id)
        ] == ["cancelled", "cancelled"]
        assert len(harness.manager.start_calls) == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_interactive_task_yields_batch_and_user_pause_prevents_auto_resume(
    tmp_path: Path,
) -> None:
    harness = create_coordinator_harness(tmp_path)
    try:
        first = harness.coordinator.create_batch(
            "alpha",
            "Alpha",
            [("chapter-1", "Chapter 1")],
        )
        second = harness.coordinator.create_batch(
            "beta",
            "Beta",
            [("chapter-1", "Chapter 1")],
        )
        harness.coordinator.start()
        await wait_for(lambda: len(harness.manager.start_calls) == 1)

        harness.manager.set_interactive(True)
        await wait_for(lambda: len(harness.manager.pause_calls) == 1)
        await wait_for(
            lambda: bool(harness.batches.batch(first.batch_id)["interactive_yielded"])
        )
        paused = harness.coordinator.pause(first.batch_id)
        assert paused.status == "paused"
        assert harness.batches.batch_items(first.batch_id)[0]["status"] == "pending"

        harness.manager.set_interactive(False)
        await wait_for(
            lambda: not bool(harness.batches.batch(first.batch_id)["interactive_yielded"])
        )
        await wait_for(lambda: len(harness.manager.start_calls) == 2)
        assert harness.manager.start_calls[1][:2] == ("beta", "chapter-1")
        harness.manager.complete("beta", "chapter-1")
        await wait_for(lambda: harness.batches.batch(second.batch_id)["status"] == "completed")
        assert harness.batches.batch(first.batch_id)["status"] == "paused"

        harness.coordinator.resume(first.batch_id)
        await wait_for(lambda: len(harness.manager.start_calls) == 3)
        assert harness.manager.start_calls[2][:2] == ("alpha", "chapter-1")
        harness.manager.complete("alpha", "chapter-1")
        await wait_for(lambda: harness.batches.batch(first.batch_id)["status"] == "completed")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_interactive_yield_auto_resumes_current_chapter_when_batch_is_running(
    tmp_path: Path,
) -> None:
    harness = create_coordinator_harness(tmp_path)
    try:
        batch = harness.coordinator.create_batch(
            "alpha",
            "Alpha",
            [("chapter-1", "Chapter 1")],
        )
        harness.coordinator.start()
        await wait_for(lambda: len(harness.manager.start_calls) == 1)
        harness.manager.set_interactive(True)
        await wait_for(lambda: len(harness.manager.pause_calls) == 1)
        harness.manager.set_interactive(False)
        await wait_for(lambda: len(harness.manager.start_calls) == 2)
        assert harness.manager.start_calls[1][:2] == ("alpha", "chapter-1")
        assert not bool(harness.batches.batch(batch.batch_id)["interactive_yielded"])
        harness.manager.complete("alpha", "chapter-1")
        await wait_for(lambda: harness.batches.batch(batch.batch_id)["status"] == "completed")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_same_chapter_interactive_task_only_pauses_batch_owned_generation(
    tmp_path: Path,
) -> None:
    harness = create_coordinator_harness(tmp_path)
    try:
        batch = harness.coordinator.create_batch(
            "alpha",
            "Alpha",
            [("chapter-1", "Chapter 1")],
        )
        harness.coordinator.start()
        await wait_for(lambda: len(harness.manager.start_calls) == 1)
        item = harness.batches.current_item(batch.batch_id)
        assert item is not None
        owned = harness.batches.owned_generation(str(item["batch_item_id"]))
        assert owned is not None
        owned_id = str(owned["generation_id"])
        interactive_id = harness.manager.seed("alpha", "chapter-1", "running")

        harness.manager.set_interactive(True)
        await wait_for(lambda: len(harness.manager.paused_generation_ids) == 1)

        assert harness.manager.paused_generation_ids == [owned_id]
        assert harness.translations.generation(owned_id)["status"] == "paused"
        assert harness.translations.generation(interactive_id)["status"] == "running"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_user_paused_yield_keeps_global_slot_until_generation_is_safely_paused(
    tmp_path: Path,
) -> None:
    harness = create_coordinator_harness(tmp_path)
    try:
        first = harness.coordinator.create_batch(
            "alpha",
            "Alpha",
            [("chapter-1", "Chapter 1")],
        )
        harness.coordinator.create_batch(
            "beta",
            "Beta",
            [("chapter-1", "Chapter 1")],
        )
        harness.manager.defer_pause = True
        harness.coordinator.start()
        await wait_for(lambda: len(harness.manager.start_calls) == 1)
        harness.manager.set_interactive(True)
        await wait_for(lambda: len(harness.manager.pause_calls) == 1)
        paused = harness.coordinator.pause(first.batch_id)
        assert paused.status == "paused"

        harness.manager.set_interactive(False)
        await asyncio.sleep(0.05)
        assert len(harness.manager.start_calls) == 1
        assert harness.batches.batch(first.batch_id)["interactive_yielded"] == 1

        harness.manager.finish_pause("alpha", "chapter-1")
        await wait_for(lambda: len(harness.manager.start_calls) == 2)
        assert harness.manager.start_calls[1][:2] == ("beta", "chapter-1")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_cancel_during_interactive_yield_finishes_current_before_next_batch(
    tmp_path: Path,
) -> None:
    harness = create_coordinator_harness(tmp_path)
    try:
        first = harness.coordinator.create_batch(
            "alpha",
            "Alpha",
            [("chapter-1", "Chapter 1"), ("chapter-2", "Chapter 2")],
        )
        harness.coordinator.create_batch(
            "beta",
            "Beta",
            [("chapter-1", "Chapter 1")],
        )
        harness.manager.defer_pause = True
        harness.coordinator.start()
        await wait_for(lambda: len(harness.manager.start_calls) == 1)
        harness.manager.set_interactive(True)
        await wait_for(lambda: len(harness.manager.pause_calls) == 1)
        cancelling = harness.coordinator.cancel_pending(first.batch_id)
        assert cancelling.status == "cancelling"
        assert [
            str(item["status"]) for item in harness.batches.batch_items(first.batch_id)
        ] == ["running", "cancelled"]

        harness.manager.set_interactive(False)
        await asyncio.sleep(0.05)
        assert len(harness.manager.start_calls) == 1
        harness.manager.finish_pause("alpha", "chapter-1")
        await wait_for(lambda: len(harness.manager.start_calls) == 2)
        assert harness.manager.start_calls[1][:2] == ("alpha", "chapter-1")
        harness.manager.complete("alpha", "chapter-1")
        await wait_for(lambda: harness.batches.batch(first.batch_id)["status"] == "cancelled")
        await wait_for(lambda: len(harness.manager.start_calls) == 3)
        assert harness.manager.start_calls[2][:2] == ("beta", "chapter-1")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_configuration_error_pauses_without_failing_item_and_can_resume(
    tmp_path: Path,
) -> None:
    harness = create_coordinator_harness(tmp_path)
    try:
        batch = harness.coordinator.create_batch(
            "alpha",
            "Alpha",
            [("chapter-1", "Chapter 1")],
        )
        harness.manager.config_error = AppError(
            "OCR_NOT_CONFIGURED",
            "请先配置 OCR",
            409,
            False,
        )
        harness.coordinator.start()
        await wait_for(lambda: harness.batches.batch(batch.batch_id)["status"] == "paused")
        row = harness.batches.batch(batch.batch_id)
        item = harness.batches.batch_items(batch.batch_id)[0]
        assert row["pause_reason"] == "config"
        assert row["error_code"] == "OCR_NOT_CONFIGURED"
        assert item["status"] == "pending"
        assert harness.manager.start_calls == []

        harness.manager.config_error = None
        harness.coordinator.resume(batch.batch_id)
        await wait_for(lambda: len(harness.manager.start_calls) == 1)
        harness.manager.complete("alpha", "chapter-1")
        await wait_for(lambda: harness.batches.batch(batch.batch_id)["status"] == "completed")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_restart_reconciles_owned_generation_without_creating_duplicate(
    tmp_path: Path,
) -> None:
    harness = create_coordinator_harness(tmp_path)
    try:
        batch = harness.coordinator.create_batch(
            "alpha",
            "Alpha",
            [("chapter-1", "Chapter 1")],
        )
        harness.coordinator.start()
        await wait_for(lambda: len(harness.manager.start_calls) == 1)
        generation = harness.translations.latest_generation("alpha", "chapter-1")
        assert generation is not None
        generation_id = str(generation["generation_id"])

        await harness.coordinator.shutdown()
        harness.translations.set_generation_status(generation_id, "queued")
        replacement = PretranslationCoordinator(
            repository=harness.batches,
            manager=harness.manager,
            poll_interval=0.01,
        )
        harness.coordinator = replacement
        replacement.start()
        await wait_for(lambda: len(harness.manager.start_calls) == 2)
        assert (
            harness.database.scalar(
                "SELECT COUNT(*) FROM translation_generations WHERE batch_item_id IS NOT NULL"
            )
            == 1
        )
        harness.manager.complete("alpha", "chapter-1")
        await wait_for(lambda: harness.batches.batch(batch.batch_id)["status"] == "completed")
    finally:
        await harness.close()
