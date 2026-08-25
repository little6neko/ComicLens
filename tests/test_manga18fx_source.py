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
async def test_creator_archives_use_kind_specific_paths_and_parse_pagination() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        fixture_name = (
            "creator-page-2.html" if request.url.path.endswith("/2") else "creator-page-1.html"
        )
        return html_response(request, fixture_name)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = Manga18fxSource(base_url="https://manga18fx.com", client=client)
        author = await source.creator("author", "author-one", 1)
        artist = await source.creator("artist", "artist-one", 2)

    assert paths == ["/manga-author/author-one", "/manga-artist/artist-one/2"]
    assert author.kind == "author"
    assert author.creator_id == "author-one"
    assert author.label == "Author One"
    assert [item.comic_id for item in author.result.items] == ["alpha-comic"]
    assert author.result.page == 1
    assert author.result.has_previous is False
    assert author.result.has_next is True
    assert artist.kind == "artist"
    assert artist.creator_id == "artist-one"
    assert artist.label == "Author One"
    assert [item.comic_id for item in artist.result.items] == ["beta-comic"]
    assert artist.result.page == 2
    assert artist.result.has_previous is True
    assert artist.result.has_next is False


@pytest.mark.asyncio
async def test_creator_archive_validates_inputs_and_maps_upstream_not_found() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = Manga18fxSource(base_url="https://manga18fx.com", client=client)
        with pytest.raises(AppError) as invalid_kind:
            await source.creator("writer", "author-one", 1)  # type: ignore[arg-type]
        with pytest.raises(AppError) as invalid_slug:
            await source.creator("author", "Not Valid", 1)
        with pytest.raises(AppError) as missing:
            await source.creator("author", "missing-author", 1)

    assert invalid_kind.value.status_code == 422
    assert invalid_slug.value.status_code == 422
    assert missing.value.code == "CREATOR_NOT_FOUND"
    assert missing.value.status_code == 404
    assert missing.value.retryable is False
    assert calls == 1


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
    assert [(item.label, item.slug) for item in detail.authors] == [("Author One", "author-one")]
    assert [(item.label, item.slug) for item in detail.artists] == [("Artist One", "artist-one")]
    assert [(item.label, item.slug) for item in detail.genres] == [
        ("Action", "action"),
        ("Fantasy", "fantasy"),
        ("School Life", "school-life"),
    ]
    assert detail.comic_type == "Webtoon"
    assert detail.release_label == "2025"
    assert detail.status == "OnGoing"
    assert [item.chapter_id for item in detail.chapters] == ["chapter-12", "chapter-11"]
    assert chapter.title == "Alpha Comic Chapter 12"
    assert [page.source_url for page in chapter.pages] == [
        "https://img01.manga18fx.com/online/1/12/1.jpg",
        "https://img01.manga18fx.com/online/1/12/2.jpg",
    ]


@pytest.mark.asyncio
async def test_detail_keeps_metadata_labels_when_links_are_untrusted() -> None:
    payload = fixture("detail.html").replace(
        b'<a href="/manga-author/author-one">Author One</a>',
        (
            b"<a>Missing Link</a>"
            b'<a href="https://example.com/manga-author/external">External</a>'
            b'<a href="/manga-artist/wrong-kind">Wrong Kind</a>'
            b'<a href="/manga-author/with-query?from=detail">With Query</a>'
            b'<a href="/manga-author/extra/path">Extra Path</a>'
            b'<a href="/manga-author/UPPERCASE">Invalid Slug</a>'
            b'<a href="/manga-author/valid-author">Valid Author</a>'
            b'<a href="/manga-author/duplicate">Duplicate</a>'
            b'<a href="/manga-author/ignored">Duplicate</a>'
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=payload,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = Manga18fxSource(base_url="https://manga18fx.com", client=client)
        detail = await source.detail("alpha-comic")

    assert [(item.label, item.slug) for item in detail.authors] == [
        ("Missing Link", None),
        ("External", None),
        ("Wrong Kind", None),
        ("With Query", None),
        ("Extra Path", None),
        ("Invalid Slug", None),
        ("Valid Author", "valid-author"),
        ("Duplicate", "duplicate"),
    ]


@pytest.mark.asyncio
async def test_detail_allows_missing_release_metadata() -> None:
    release_item = (
        b'<div class="post-content_item"><div class="summary-heading">'
        b'<h5>Release</h5></div><div class="summary-content">2025</div></div>'
    )
    payload = fixture("detail.html").replace(
        release_item,
        b"",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=payload,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = Manga18fxSource(base_url="https://manga18fx.com", client=client)
        detail = await source.detail("alpha-comic")

    assert detail.release_label is None


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
async def test_configured_proxy_is_the_only_route_for_html_and_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct_requests = 0
    proxy_requests: list[str] = []
    proxy_urls: list[str] = []

    def direct_handler(request: httpx.Request) -> httpx.Response:
        nonlocal direct_requests
        direct_requests += 1
        raise AssertionError(f"persistent client must not be used: {request.url}")

    def proxy_handler(request: httpx.Request) -> httpx.Response:
        proxy_requests.append(str(request.url))
        if request.url.host == "manga18fx.com":
            return html_response(request, "home.html")
        return httpx.Response(
            200,
            headers={"content-type": "image/jpeg"},
            content=b"proxy-image",
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(direct_handler)) as direct_client:
        source = Manga18fxSource(
            base_url="https://manga18fx.com",
            client=direct_client,
            proxy_provider=lambda: "http://user:secret@proxy.example:8080",
        )

        def proxy_factory(proxy_url: str) -> httpx.AsyncClient:
            proxy_urls.append(proxy_url)
            return httpx.AsyncClient(transport=httpx.MockTransport(proxy_handler))

        monkeypatch.setattr(source, "_proxy_client", proxy_factory)
        home = await source.home()
        media, content_type = await source.fetch_media(
            "https://img01.manga18fx.com/online/1/12/1.jpg"
        )

    assert home.latest.items
    assert media == b"proxy-image"
    assert content_type == "image/jpeg"
    assert direct_requests == 0
    assert proxy_requests == [
        "https://manga18fx.com/",
        "https://img01.manga18fx.com/online/1/12/1.jpg",
    ]
    assert proxy_urls == [
        "http://user:secret@proxy.example:8080",
        "http://user:secret@proxy.example:8080",
    ]


@pytest.mark.asyncio
async def test_configured_proxy_failure_retries_only_the_proxy_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct_requests = 0
    proxy_requests = 0

    def direct_handler(request: httpx.Request) -> httpx.Response:
        nonlocal direct_requests
        direct_requests += 1
        raise AssertionError(f"persistent client must not be used: {request.url}")

    def proxy_handler(request: httpx.Request) -> httpx.Response:
        nonlocal proxy_requests
        proxy_requests += 1
        raise httpx.ConnectTimeout("proxy timeout", request=request)

    monkeypatch.setattr("app.sources.manga18fx.asyncio.sleep", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(direct_handler)) as direct_client:
        source = Manga18fxSource(
            base_url="https://manga18fx.com",
            client=direct_client,
            proxy_provider=lambda: "http://user:secret@proxy.example:8080",
        )
        monkeypatch.setattr(
            source,
            "_proxy_client",
            lambda _proxy_url: httpx.AsyncClient(transport=httpx.MockTransport(proxy_handler)),
        )

        with pytest.raises(AppError) as captured:
            await source.home()

    assert direct_requests == 0
    assert proxy_requests == 3
    assert captured.value.code == "UPSTREAM_FETCH_ERROR"
    assert "user" not in captured.value.message
    assert "secret" not in captured.value.message
    assert "proxy.example" not in captured.value.message


@pytest.mark.asyncio
async def test_configured_proxy_keeps_safe_redirects_on_the_proxy_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct_requests = 0
    proxy_paths: list[str] = []
    proxy_clients = 0

    def direct_handler(request: httpx.Request) -> httpx.Response:
        nonlocal direct_requests
        direct_requests += 1
        raise AssertionError(f"persistent client must not be used: {request.url}")

    def proxy_handler(request: httpx.Request) -> httpx.Response:
        proxy_paths.append(request.url.path)
        if len(proxy_paths) == 1:
            return httpx.Response(302, headers={"location": "/page/2"}, request=request)
        return html_response(request, "list.html")

    async with httpx.AsyncClient(transport=httpx.MockTransport(direct_handler)) as direct_client:
        source = Manga18fxSource(
            base_url="https://manga18fx.com",
            client=direct_client,
            proxy_provider=lambda: "http://proxy.example:8080",
        )

        def proxy_factory(_proxy_url: str) -> httpx.AsyncClient:
            nonlocal proxy_clients
            proxy_clients += 1
            return httpx.AsyncClient(transport=httpx.MockTransport(proxy_handler))

        monkeypatch.setattr(source, "_proxy_client", proxy_factory)
        result = await source.latest(2)

    assert result.items
    assert direct_requests == 0
    assert proxy_clients == 1
    assert proxy_paths == ["/page/2", "/page/2"]


@pytest.mark.asyncio
async def test_unconfigured_proxy_uses_persistent_client_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return html_response(request, "home.html")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = Manga18fxSource(
            base_url="https://manga18fx.com",
            client=client,
            proxy_provider=lambda: "  ",
        )
        proxy_factory = AsyncMock()
        monkeypatch.setattr(source, "_proxy_client", proxy_factory)
        result = await source.home()

    assert result.latest.items
    assert requests == ["https://manga18fx.com/"]
    proxy_factory.assert_not_called()


@pytest.mark.asyncio
async def test_default_persistent_client_trusts_proxy_environment() -> None:
    source = Manga18fxSource(base_url="https://manga18fx.com")
    try:
        assert source.client._trust_env is True
    finally:
        await source.aclose()
