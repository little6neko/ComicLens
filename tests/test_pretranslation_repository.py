from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.repositories.database import Database
from app.repositories.pretranslation import PretranslationRepository
from app.repositories.translation import TranslationRepository


@pytest.fixture
def batch_repository(tmp_path: Path):
    database = Database(tmp_path / "pretranslation.db")
    repository = PretranslationRepository(database)
    try:
        yield repository
    finally:
        database.close()


def test_create_batch_persists_order_and_enforces_one_open_batch_per_comic(
    batch_repository: PretranslationRepository,
) -> None:
    batch_id = batch_repository.create_batch(
        "alpha",
        "Alpha",
        [("special", "Special"), ("chapter-2", "Chapter 2")],
    )

    summary = batch_repository.summary(batch_id)
    assert summary is not None
    assert summary.status == "queued"
    assert summary.total_chapters == 2
    assert summary.pending_chapters == 2
    assert [
        (str(row["chapter_id"]), int(row["position"]))
        for row in batch_repository.batch_items(batch_id)
    ] == [("special", 0), ("chapter-2", 1)]

    with pytest.raises(sqlite3.IntegrityError):
        batch_repository.create_batch("alpha", "Alpha", [("chapter-3", "Chapter 3")])

    second_id = batch_repository.create_batch(
        "beta",
        "Beta",
        [("chapter-1", "Chapter 1")],
    )
    assert batch_repository.open_batch_for_comic("alpha")["batch_id"] == batch_id
    assert batch_repository.open_batch_for_comic("beta")["batch_id"] == second_id


def test_summary_separates_processed_success_from_currently_translated_chapters(
    batch_repository: PretranslationRepository,
) -> None:
    batch_id = batch_repository.create_batch(
        "alpha",
        "Alpha",
        [("chapter-1", "Chapter 1"), ("chapter-2", "Chapter 2")],
    )
    assert batch_repository.claim_batch(batch_id) is True
    first_item = batch_repository.batch_items(batch_id)[0]
    assert batch_repository.claim_item(str(first_item["batch_item_id"])) is True
    assert batch_repository.finish_item(str(first_item["batch_item_id"]), "completed") is True

    translations = TranslationRepository(batch_repository.database)
    generation_id = translations.create_generation(
        "alpha",
        "chapter-1",
        semantic_fingerprint="completed",
        semantic_settings={},
        page_indexes=[],
        kind="normal",
    )
    translations.set_generation_status(generation_id, "completed")

    available = batch_repository.summary(batch_id)
    assert available is not None
    assert available.completed_chapters == 1
    assert available.available_chapters == 1

    batch_repository.database.execute(
        "DELETE FROM translation_generations WHERE generation_id = ?",
        (generation_id,),
    )
    evicted = batch_repository.summary(batch_id)
    assert evicted is not None
    assert evicted.completed_chapters == 1
    assert evicted.available_chapters == 0


def test_create_batch_rolls_back_when_item_constraints_fail(
    batch_repository: PretranslationRepository,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        batch_repository.create_batch(
            "alpha",
            "Alpha",
            [("chapter-1", "Chapter 1"), ("chapter-1", "Duplicate")],
        )

    assert batch_repository.open_batch_for_comic("alpha") is None
    assert batch_repository.database.scalar("SELECT COUNT(*) FROM translation_batch_items") == 0


def test_scheduler_keeps_current_batch_and_claims_only_one_item(
    batch_repository: PretranslationRepository,
) -> None:
    first_id = batch_repository.create_batch(
        "alpha",
        "Alpha",
        [("chapter-1", "Chapter 1"), ("chapter-2", "Chapter 2")],
    )
    second_id = batch_repository.create_batch(
        "beta",
        "Beta",
        [("chapter-1", "Chapter 1")],
    )

    assert batch_repository.scheduler_batch()["batch_id"] == first_id
    assert batch_repository.claim_batch(first_id) is True
    first_item, second_item = batch_repository.batch_items(first_id)
    assert batch_repository.claim_item(str(first_item["batch_item_id"])) is True
    assert batch_repository.claim_item(str(second_item["batch_item_id"])) is False
    assert batch_repository.scheduler_batch()["batch_id"] == first_id

    assert batch_repository.finish_item(str(first_item["batch_item_id"]), "completed") is True
    batch_repository.settle_after_item(first_id)
    assert batch_repository.claim_item(str(second_item["batch_item_id"])) is True
    assert batch_repository.finish_item(str(second_item["batch_item_id"]), "skipped") is True
    settled = batch_repository.settle_after_item(first_id)
    assert settled is not None and settled["status"] == "completed"
    assert batch_repository.scheduler_batch()["batch_id"] == second_id


def test_pause_reason_and_interactive_yield_are_independent(
    batch_repository: PretranslationRepository,
) -> None:
    batch_id = batch_repository.create_batch(
        "alpha",
        "Alpha",
        [("chapter-1", "Chapter 1")],
    )

    paused = batch_repository.request_pause(batch_id)
    assert paused is not None
    assert paused["status"] == "paused"
    assert paused["pause_reason"] == "user"
    batch_repository.set_interactive_yielded(batch_id, True)

    resumed = batch_repository.resume_batch(batch_id)
    assert resumed is not None
    assert resumed["status"] == "queued"
    assert resumed["pause_reason"] is None
    assert resumed["interactive_yielded"] == 1
    batch_repository.set_interactive_yielded(batch_id, False)

    batch_repository.claim_batch(batch_id)
    item = batch_repository.next_pending_item(batch_id)
    assert item is not None
    batch_repository.claim_item(str(item["batch_item_id"]))
    pausing = batch_repository.request_pause(batch_id)
    assert pausing is not None and pausing["status"] == "pausing"
    assert pausing["pause_reason"] == "user"
    batch_repository.set_interactive_yielded(batch_id, True)

    batch_repository.finish_item(str(item["batch_item_id"]), "completed")
    settled = batch_repository.settle_after_item(batch_id)
    assert settled is not None and settled["status"] == "paused"
    assert settled["interactive_yielded"] == 1
    cleared = batch_repository.set_interactive_yielded(batch_id, False)
    assert cleared is not None and cleared["pause_reason"] == "user"


def test_cancel_pending_finishes_current_item_then_closes_batch(
    batch_repository: PretranslationRepository,
) -> None:
    batch_id = batch_repository.create_batch(
        "alpha",
        "Alpha",
        [("chapter-1", "Chapter 1"), ("chapter-2", "Chapter 2")],
    )
    batch_repository.claim_batch(batch_id)
    first_item, second_item = batch_repository.batch_items(batch_id)
    batch_repository.claim_item(str(first_item["batch_item_id"]))

    cancelling = batch_repository.cancel_pending(batch_id)
    assert cancelling is not None and cancelling["status"] == "cancelling"
    assert batch_repository.batch_item(str(second_item["batch_item_id"]))["status"] == "cancelled"

    repeated = batch_repository.cancel_pending(batch_id)
    assert repeated is not None and repeated["status"] == "cancelling"
    batch_repository.finish_item(str(first_item["batch_item_id"]), "completed")
    cancelled = batch_repository.settle_after_item(batch_id)
    assert cancelled is not None and cancelled["status"] == "cancelled"


def test_failed_items_can_be_requeued_without_touching_successful_items(
    batch_repository: PretranslationRepository,
) -> None:
    batch_id = batch_repository.create_batch(
        "alpha",
        "Alpha",
        [("chapter-1", "Chapter 1"), ("chapter-2", "Chapter 2")],
    )
    batch_repository.claim_batch(batch_id)
    first_item, second_item = batch_repository.batch_items(batch_id)
    batch_repository.claim_item(str(first_item["batch_item_id"]))
    batch_repository.finish_item(str(first_item["batch_item_id"]), "completed")
    batch_repository.settle_after_item(batch_id)
    batch_repository.claim_item(str(second_item["batch_item_id"]))
    batch_repository.finish_item(
        str(second_item["batch_item_id"]),
        "failed",
        error_code="OCR_FAILED",
        error_summary="OCR failed",
    )
    settled = batch_repository.settle_after_item(batch_id)
    assert settled is not None and settled["status"] == "completed_with_errors"

    retried, count = batch_repository.retry_failed(batch_id)
    assert retried is not None and retried["status"] == "queued"
    assert count == 1
    refreshed_first, refreshed_second = batch_repository.batch_items(batch_id)
    assert refreshed_first["status"] == "completed"
    assert refreshed_second["status"] == "pending"
    assert refreshed_second["error_code"] is None

    repeated, repeated_count = batch_repository.retry_failed(batch_id)
    assert repeated is not None and repeated["status"] == "queued"
    assert repeated_count == 0


def test_failed_batch_can_be_resumed_or_closed_idempotently(
    batch_repository: PretranslationRepository,
) -> None:
    batch_id = batch_repository.create_batch(
        "alpha",
        "Alpha",
        [("chapter-1", "Chapter 1")],
    )
    failed = batch_repository.set_batch_failed(
        batch_id,
        error_code="COORDINATOR_ERROR",
        error_summary="Coordinator stopped",
    )
    assert failed is not None and failed["status"] == "failed"

    resumed = batch_repository.resume_batch(batch_id)
    assert resumed is not None and resumed["status"] == "queued"
    assert resumed["error_code"] is None
    batch_repository.set_batch_failed(
        batch_id,
        error_code="COORDINATOR_ERROR",
        error_summary="Coordinator stopped again",
    )
    closed = batch_repository.close_failed_batch(batch_id)
    assert closed is not None and closed["status"] == "cancelled"
    repeated = batch_repository.close_failed_batch(batch_id)
    assert repeated is not None and repeated["status"] == "cancelled"

    replacement = batch_repository.create_batch(
        "alpha",
        "Alpha",
        [("chapter-2", "Chapter 2")],
    )
    assert replacement != batch_id
