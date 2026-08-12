from __future__ import annotations

import gzip
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from app.errors import AppError
from app.sources.manga18fx import CATEGORY_BASELINE, Manga18fxSource

FIXTURES = Path(__file__).parent / "fixtures" / "manga18fx"


def fixture(name: str) -> bytes:
    return FIXTURES.joinpath(name).read_bytes()


def html_response(request: httpx.Request, name: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"content-type": "text/html; charset=utf-8"},
        content=fixture(name),
        request=request,
    )


@pytest.mark.asyncio
async def test_home_separates_featured_track_from_latest_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return html_response(request, "home.html")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = Manga18fxSource(base_url="https://manga18fx.com", client=client)
        result = await source.home()

    assert [item.comic_id for item in result.featured] == ["alpha-comic"]
    assert [item.comic_id for item in result.latest.items] == ["alpha-comic"]


@pytest.mark.asyncio
async def test_search_uses_query_pagination_and_parses_list() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return html_response(request, "list.html")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = Manga18fxSource(base_url="https://manga18fx.com", client=client)
        result = await source.search("alpha & beta", 2)

    assert requests[0].url.path == "/search"
    assert requests[0].url.params.get("q") == "alpha & beta"
    assert requests[0].url.params.get("page") == "2"
    assert result.page == 2
    assert result.available_pages == [1, 2, 3]
    assert result.has_previous is True
    assert result.has_next is True
    assert [item.comic_id for item in result.items] == ["alpha-comic", "beta-comic"]
    assert result.items[0].rating == 4.7
    assert result.items[0].is_adult is True
    assert result.items[1].rating is None
    assert result.items[0].latest_chapters[1].updated_label is None


@pytest.mark.asyncio
async def test_empty_search_is_not_treated_as_parse_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return html_response(request, "empty-search.html")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = Manga18fxSource(base_url="https://manga18fx.com", client=client)
        result = await source.search("missing", 1)

    assert result.items == []
    assert result.page == 1


@pytest.mark.asyncio
async def test_category_and_ranking_use_distinct_upstream_pagination() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(str(request.url))
        return html_response(request, "list.html")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = Manga18fxSource(base_url="https://manga18fx.com", client=client)
        await source.category("raw", 2, "views")
        await source.category("source:raw-feed", 2, "rating")
        await source.ranking(2)

    assert paths == [
        "https://manga18fx.com/manga-genre/raw/2?orderby=views",
        "https://manga18fx.com/manhwa-raw/2?orderby=rating",
        "https://manga18fx.com/hot-manga?page=2",
    ]


@pytest.mark.asyncio
async def test_categories_use_sitemap_and_validate_new_slugs() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/":
            return html_response(request, "home.html")
        if request.url.path == "/sitemap-manga.xml.gz":
            return httpx.Response(
                200,
                content=gzip.compress(fixture("sitemap.xml")),
                request=request,
            )
        if request.url.path == "/manga-genre/new-genre":
            return html_response(request, "home.html")
        raise AssertionError(f"unexpected URL {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = Manga18fxSource(base_url="https://manga18fx.com", client=client)
        result = await source.categories()

    assert [item.category_id for item in result] == [
        "action",
        "new-genre",
        "source:raw-feed",
    ]
    assert result[0].label == "Action"
    assert result[1].label == "New Genre"
    assert requested_paths.count("/manga-genre/new-genre") == 1


@pytest.mark.asyncio
async def test_categories_fall_back_to_full_baseline_when_sitemap_is_invalid() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return html_response(request, "home.html")
        if request.url.path == "/sitemap-manga.xml.gz":
            return httpx.Response(200, content=b"not xml", request=request)
        if request.url.path == "/manga-genre/new-genre":
            return httpx.Response(404, request=request)
        raise AssertionError(f"unexpected URL {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = Manga18fxSource(base_url="https://manga18fx.com", client=client)
        result = await source.categories()

    assert [item.category_id for item in result[:-1]] == list(CATEGORY_BASELINE)
    assert result[-1].category_id == "source:raw-feed"


@pytest.mark.asyncio
async def test_detail_and_chapter_only_parse_scoped_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("chapter-12"):
            return html_response(request, "chapter.html")
        return html_response(request, "detail.html")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = Manga18fxSource(base_url="https://manga18fx.com", client=client)
        detail = await source.detail("alpha-comic")
        chapter = await source.chapter("alpha-comic", "chapter-12")

    assert detail.title == "Alpha Comic"
    assert detail.alternative_titles == ["Alpha", "阿尔法"]
    assert detail.authors == ["Author One"]
    assert detail.genres == ["Action", "Fantasy"]
    assert detail.comic_type == "Webtoon"
    assert detail.status == "OnGoing"
    assert [item.chapter_id for item in detail.chapters] == ["chapter-12", "chapter-11"]
    assert chapter.title == "Alpha Comic Chapter 12"
    assert [page.source_url for page in chapter.pages] == [
        "https://img01.manga18fx.com/online/1/12/1.jpg",
        "https://img01.manga18fx.com/online/1/12/2.jpg",
    ]


@pytest.mark.asyncio
async def test_unknown_category_and_external_media_are_rejected_without_request() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = Manga18fxSource(base_url="https://manga18fx.com", client=client)
        with pytest.raises(AppError) as category_error:
            await source.category("not-a-real-category", 1, "latest")
        with pytest.raises(ValueError):
            await source.fetch_media("https://example.com/private.png")

    assert category_error.value.status_code == 404
    assert calls == 0


@pytest.mark.asyncio
async def test_retryable_direct_failure_uses_configured_fallback_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct_requests = 0
    proxy_requests = 0
    proxy_urls: list[str] = []

    def direct_handler(request: httpx.Request) -> httpx.Response:
        nonlocal direct_requests
        direct_requests += 1
        raise httpx.ConnectTimeout("direct timeout", request=request)

    def proxy_handler(request: httpx.Request) -> httpx.Response:
        nonlocal proxy_requests
        proxy_requests += 1
        return html_response(request, "home.html")

    monkeypatch.setattr("app.sources.manga18fx.asyncio.sleep", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(direct_handler)) as direct_client:
        proxy_client = httpx.AsyncClient(transport=httpx.MockTransport(proxy_handler))
        source = Manga18fxSource(
            base_url="https://manga18fx.com",
            client=direct_client,
            fallback_proxy_provider=lambda: "http://user:secret@proxy.example:8080",
        )

        def proxy_factory(proxy_url: str) -> httpx.AsyncClient:
            proxy_urls.append(proxy_url)
            return proxy_client

        monkeypatch.setattr(source, "_proxy_client", proxy_factory)
        result = await source.home()

    assert result.latest.items
    assert direct_requests == 3
    assert proxy_requests == 1
    assert proxy_urls == ["http://user:secret@proxy.example:8080"]
