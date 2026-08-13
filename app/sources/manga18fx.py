from __future__ import annotations

import asyncio
import gzip
import re
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.domain.comic import (
    ChapterSummary,
    ComicCategory,
    ComicChapter,
    ComicDetail,
    ComicListPage,
    ComicSummary,
    FeaturedComic,
    HomeFeed,
    SourceChapterManifest,
    SourcePage,
)
from app.errors import AppError
from app.sources.base import ComicOrder

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COMIC_PATH_PATTERN = re.compile(r"^/manga/([^/]+)/?$")
CHAPTER_PATH_PATTERN = re.compile(r"^/manga/([^/]+)/([^/]+)/?$")
CATEGORY_PATH_PATTERN = re.compile(r"^/manga-genre/([^/?#]+)/?$")
ALLOWED_ORDERS: tuple[ComicOrder, ...] = ("latest", "rating", "views")
SPECIAL_RAW_CATEGORY_ID = "source:raw-feed"
MAX_SOURCE_PAGES = 500

CATEGORY_BASELINE = (
    "action",
    "adult",
    "adventure",
    "bl",
    "comedy",
    "comics",
    "cooking",
    "demons",
    "doujinshi",
    "drama",
    "ecchi",
    "family",
    "fantasy",
    "game",
    "gender-bender",
    "gl",
    "harem",
    "hentai",
    "historical",
    "horror",
    "isekai",
    "josei",
    "magic",
    "manhua",
    "manhwa",
    "martial-arts",
    "mature",
    "mecha",
    "mystery",
    "ntr",
    "psychological",
    "raw",
    "reincarnation",
    "romance",
    "rpg",
    "school-life",
    "sci-fi",
    "seinen",
    "shoujo",
    "shounen",
    "slice-of-life",
    "smut",
    "sports",
    "super-power",
    "supernatural",
    "thriller",
    "tragedy",
    "uncensored-manhwa",
    "vanilla",
    "webtoon",
    "webtoons",
    "yaoi",
    "yuri",
    "zombie",
)


class Manga18fxSource:
    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 30.0,
        user_agent: str | None = None,
        client: httpx.AsyncClient | None = None,
        fallback_proxy_provider: Callable[[], str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.base_host = (urlparse(self.base_url).hostname or "").lower()
        if not self.base_host:
            raise RuntimeError("Comic source base URL is invalid")
        self._owns_client = client is None
        self._known_category_ids = set(CATEGORY_BASELINE)
        self._timeout = httpx.Timeout(timeout)
        self._headers = {
            "User-Agent": user_agent
            or (
                "Mozilla/5.0 (Linux; Android 14; Mobile) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Mobile Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        self._fallback_proxy_provider = fallback_proxy_provider
        self.client = client or httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
            headers=self._headers,
            trust_env=False,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def home(self) -> HomeFeed:
        soup = await self._get_html("/")
        featured = self._parse_featured(soup)
        latest = self._parse_list_page(soup, requested_page=1)
        if not featured and not latest.items:
            raise self._parse_error("首页内容结构无法识别")
        return HomeFeed(featured=featured, latest=latest)

    async def latest(self, page: int) -> ComicListPage:
        self._validate_page(page)
        soup = await self._get_html(f"/page/{page}")
        return self._parse_list_page(soup, requested_page=page)

    async def search(self, query: str, page: int) -> ComicListPage:
        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise AppError("VALIDATION_ERROR", "搜索词不能为空", 422, False)
        self._validate_page(page)
        query_string = str(httpx.QueryParams({"q": normalized_query, "page": page}))
        path = f"/search?{query_string}"
        soup = await self._get_html(path)
        return self._parse_list_page(soup, requested_page=page, allow_empty=True)

    async def categories(self) -> list[ComicCategory]:
        home_task = asyncio.create_task(self._get_html("/"))
        sitemap_task = asyncio.create_task(self._get_bytes("/sitemap-manga.xml.gz"))
        home_result, sitemap_result = await asyncio.gather(
            home_task, sitemap_task, return_exceptions=True
        )

        home_labels: dict[str, str] = {}
        if isinstance(home_result, BeautifulSoup):
            home_labels = self._parse_home_categories(home_result)

        sitemap_slugs: list[str] | None = None
        if isinstance(sitemap_result, bytes):
            try:
                sitemap_slugs = self._parse_sitemap_categories(sitemap_result)
            except AppError:
                sitemap_slugs = None

        slugs = sitemap_slugs if sitemap_slugs else list(CATEGORY_BASELINE)
        unverified_slugs = {
            slug for slug in [*slugs, *home_labels] if slug not in CATEGORY_BASELINE
        }
        if unverified_slugs:
            validations = await asyncio.gather(
                *(self._category_exists(slug) for slug in sorted(unverified_slugs))
            )
            verified_slugs = {
                slug
                for slug, is_valid in zip(sorted(unverified_slugs), validations, strict=True)
                if is_valid
            }
            slugs = [slug for slug in slugs if slug in CATEGORY_BASELINE or slug in verified_slugs]
            for discovered_slug in home_labels:
                if discovered_slug in verified_slugs and discovered_slug not in slugs:
                    slugs.append(discovered_slug)

        self._known_category_ids.update(slugs)

        categories = [
            ComicCategory(
                category_id=slug,
                label=home_labels.get(slug, self._format_slug(slug)),
            )
            for slug in slugs
        ]
        categories.append(
            ComicCategory(
                category_id=SPECIAL_RAW_CATEGORY_ID,
                label="Manhwa Raw",
                kind="source_special",
            )
        )
        return categories

    async def category(self, category_id: str, page: int, order: ComicOrder) -> ComicListPage:
        self._validate_page(page)
        self._validate_order(order)
        if category_id == SPECIAL_RAW_CATEGORY_ID:
            base_path = "/manhwa-raw" if page == 1 else f"/manhwa-raw/{page}"
        else:
            self._validate_slug(category_id, "分类")
            if category_id not in self._known_category_ids:
                raise AppError("CATEGORY_NOT_FOUND", "分类不存在", 404, False)
            base_path = (
                f"/manga-genre/{category_id}" if page == 1 else f"/manga-genre/{category_id}/{page}"
            )
        soup = await self._get_html(f"{base_path}?orderby={order}")
        return self._parse_list_page(soup, requested_page=page)

    async def ranking(self, page: int) -> ComicListPage:
        self._validate_page(page)
        soup = await self._get_html(f"/hot-manga?page={page}")
        return self._parse_list_page(soup, requested_page=page)

    async def detail(self, comic_id: str) -> ComicDetail:
        self._validate_slug(comic_id, "Comic")
        soup = await self._get_html(f"/manga/{comic_id}")
        title_node = soup.select_one(".post-title h1")
        cover_node = soup.select_one(".summary_image img")
        if title_node is None or cover_node is None:
            raise self._parse_error("Comic 详情结构无法识别")

        metadata = self._parse_detail_metadata(soup)
        rating = self._parse_float(self._text(soup.select_one("#averagerate")))
        chapters: list[ComicChapter] = []
        for node in soup.select("#chapterlist .row-content-chapter > li"):
            link = node.select_one("a.chapter-name[href]")
            chapter_id = self._extract_chapter_id(link, expected_comic_id=comic_id)
            if link is None or chapter_id is None:
                continue
            chapters.append(
                ComicChapter(
                    chapter_id=chapter_id,
                    title=self._text(link) or chapter_id,
                    updated_label=self._optional_text(node.select_one(".chapter-time")),
                )
            )

        if not chapters:
            raise self._parse_error("Comic 章节列表无法识别")

        alternative = self._metadata_text(metadata, "alternative")
        return ComicDetail(
            comic_id=comic_id,
            title=self._text(title_node),
            cover_url=self._image_url(cover_node),
            rating=rating,
            alternative_titles=[part.strip() for part in alternative.split("/") if part.strip()],
            authors=self._metadata_links(metadata, "author(s)"),
            artists=self._metadata_links(metadata, "artist(s)"),
            genres=self._metadata_links(metadata, "genre(s)"),
            comic_type=self._metadata_text(metadata, "type") or None,
            release_label=self._metadata_text(metadata, "release") or None,
            status=self._metadata_text(metadata, "status") or None,
            summary=self._text(soup.select_one(".panel-story-description .dsct")),
            chapters=chapters,
        )

    async def chapter(self, comic_id: str, chapter_id: str) -> SourceChapterManifest:
        self._validate_slug(comic_id, "Comic")
        self._validate_slug(chapter_id, "章节")
        soup = await self._get_html(f"/manga/{comic_id}/{chapter_id}")
        pages: list[SourcePage] = []
        seen_urls: set[str] = set()
        for image in soup.select(".page-break img"):
            source_url = self._image_url(image)
            if not source_url or source_url in seen_urls:
                continue
            self._validate_media_url(source_url)
            seen_urls.add(source_url)
            pages.append(
                SourcePage(
                    index=len(pages),
                    source_url=source_url,
                    alt=str(image.get("alt") or "").strip(),
                )
            )
        if not pages:
            raise self._parse_error("章节图片结构无法识别")
        if len(pages) > MAX_SOURCE_PAGES:
            raise self._parse_error("章节图片数量超过防御性上限")

        page_title = self._text(soup.select_one("title"))
        title = page_title.rsplit(" - Manga18fx", 1)[0] if page_title else chapter_id
        return SourceChapterManifest(
            comic_id=comic_id,
            chapter_id=chapter_id,
            title=title,
            pages=pages,
        )

    async def fetch_media(self, source_url: str) -> tuple[bytes, str]:
        self._validate_media_url(source_url)
        response = await self._request(source_url, allow_media_host=True)
        content_type = response.headers.get("content-type", "application/octet-stream")
        if not content_type.lower().startswith("image/"):
            raise AppError(
                "UPSTREAM_MEDIA_ERROR",
                "来源返回的内容不是图片",
                502,
                True,
            )
        return response.content, content_type.split(";", 1)[0]

    async def _get_html(self, path: str) -> BeautifulSoup:
        response = await self._request(self._url(path), allow_media_host=False)
        content_type = response.headers.get("content-type", "").lower()
        if "html" not in content_type and not response.text.lstrip().startswith("<"):
            raise self._parse_error("来源未返回 HTML")
        return BeautifulSoup(response.text, "lxml")

    async def _get_bytes(self, path: str) -> bytes:
        response = await self._request(self._url(path), allow_media_host=False)
        return response.content

    async def _request(self, url: str, *, allow_media_host: bool) -> httpx.Response:
        last_error: Exception | None = None
        try:
            return await self._request_with_retries(
                self.client,
                url,
                allow_media_host=allow_media_host,
            )
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc

        proxy_url = self._fallback_proxy_url() if self._is_retryable(last_error) else ""
        if proxy_url:
            try:
                async with self._proxy_client(proxy_url) as proxy_client:
                    return await self._request_with_retries(
                        proxy_client,
                        url,
                        allow_media_host=allow_media_host,
                    )
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc

        raise AppError(
            "UPSTREAM_FETCH_ERROR",
            "无法读取 Manga18fx，请稍后重试",
            502,
            True,
        ) from last_error

    async def _request_with_retries(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        allow_media_host: bool,
    ) -> httpx.Response:
        for attempt in range(3):
            try:
                response = await self._request_with_safe_redirects(
                    client,
                    url,
                    allow_media_host=allow_media_host,
                )
                if (response.status_code == 429 or response.status_code >= 500) and attempt < 2:
                    retry_after = response.headers.get("retry-after", "")
                    delay = float(retry_after) if retry_after.isdigit() else 0.2 * (2**attempt)
                    await asyncio.sleep(min(delay, 2.0))
                    continue
                response.raise_for_status()
                return response
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < 2 and isinstance(exc, httpx.TimeoutException | httpx.NetworkError):
                    await asyncio.sleep(0.2 * (2**attempt))
                    continue
                raise
        assert last_error is not None
        raise last_error

    async def _request_with_safe_redirects(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        allow_media_host: bool,
    ) -> httpx.Response:
        current_url = url
        for _redirect in range(4):
            self._validate_final_url(current_url, media=allow_media_host)
            response = await client.get(current_url, follow_redirects=False)
            if not response.is_redirect:
                return response
            location = response.headers.get("location")
            if not location:
                raise ValueError("source returned a redirect without a location")
            current_url = urljoin(current_url, location)
            self._validate_final_url(current_url, media=allow_media_host)
        raise ValueError("source returned too many redirects")

    def _fallback_proxy_url(self) -> str:
        if self._fallback_proxy_provider is None:
            return ""
        return str(self._fallback_proxy_provider() or "").strip()

    def _proxy_client(self, proxy_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
            headers=self._headers,
            proxy=proxy_url,
            trust_env=False,
        )

    @staticmethod
    def _is_retryable(error: Exception | None) -> bool:
        if isinstance(error, httpx.TimeoutException | httpx.NetworkError):
            return True
        if isinstance(error, httpx.HTTPStatusError):
            return error.response.status_code == 429 or error.response.status_code >= 500
        return False

    async def _category_exists(self, slug: str) -> bool:
        try:
            soup = await self._get_html(f"/manga-genre/{slug}?orderby=latest")
            self._parse_list_page(soup, requested_page=1)
        except AppError:
            return False
        return True

    def _parse_list_page(
        self,
        soup: BeautifulSoup,
        *,
        requested_page: int,
        allow_empty: bool = False,
    ) -> ComicListPage:
        containers = soup.select(".manga-content .listupd") or soup.select(".listupd")
        container: Tag | None = None
        item_nodes: list[Tag] = []
        for candidate in containers:
            candidate_items = candidate.select(":scope > .page-item")
            if candidate_items:
                container = candidate
                item_nodes = candidate_items
                break
        if container is None and allow_empty:
            container = next(
                (
                    candidate
                    for candidate in containers
                    if "no result" in self._text(candidate).lower()
                ),
                None,
            )
        if container is None:
            raise self._parse_error("列表内容容器不存在")

        items: list[ComicSummary] = []
        for node in item_nodes:
            parsed = self._parse_list_item(node)
            if parsed is not None:
                items.append(parsed)

        no_result = "no result" in self._text(container).lower()
        if not items and not (allow_empty and no_result):
            raise self._parse_error("列表项结构无法识别")

        page, available_pages, has_previous, has_next = self._parse_pagination(soup, requested_page)
        return ComicListPage(
            items=items,
            page=page,
            available_pages=available_pages,
            has_previous=has_previous,
            has_next=has_next,
        )

    def _parse_list_item(self, node: Tag) -> ComicSummary | None:
        link = node.select_one("h3.tt a[href]") or node.select_one(".thumb-manga a[href]")
        comic_id = self._extract_comic_id(link)
        image = node.select_one(".thumb-manga img")
        if link is None or comic_id is None or image is None:
            return None
        title = str(link.get("title") or "").strip() or self._text(link)
        if not title:
            title = str(image.get("alt") or "").strip()
        if not title:
            return None

        latest_chapters: list[ChapterSummary] = []
        for chapter_node in node.select(".list-chapter .chapter-item"):
            chapter_link = chapter_node.select_one("a.btn-link[href]")
            chapter_id = self._extract_chapter_id(chapter_link, expected_comic_id=comic_id)
            if chapter_link is None or chapter_id is None:
                continue
            latest_chapters.append(
                ChapterSummary(
                    chapter_id=chapter_id,
                    title=self._text(chapter_link) or chapter_id,
                    updated_label=self._optional_text(chapter_node.select_one(".post-on")),
                )
            )

        rating_node = node.select_one(".mmrate[data-rating]")
        rating = (
            self._parse_float(str(rating_node.get("data-rating") or "")) if rating_node else None
        )
        return ComicSummary(
            comic_id=comic_id,
            title=title,
            cover_url=self._image_url(image),
            rating=rating,
            is_adult=node.select_one(".adult-badges") is not None,
            latest_chapters=latest_chapters,
        )

    def _parse_featured(self, soup: BeautifulSoup) -> list[FeaturedComic]:
        items: list[FeaturedComic] = []
        for node in soup.select(".trending-block .hot-item"):
            link = node.select_one("a[href]")
            image = node.select_one("img")
            comic_id = self._extract_comic_id(link)
            if link is None or image is None or comic_id is None:
                continue
            title_node = node.select_one("h3")
            title = self._text(title_node) or str(link.get("title") or "").strip()
            if not title:
                continue
            items.append(
                FeaturedComic(
                    comic_id=comic_id,
                    title=title,
                    cover_url=self._image_url(image),
                    chapter_label=self._optional_text(node.select_one(".chapter-badges")),
                )
            )
        return items

    def _parse_pagination(
        self, soup: BeautifulSoup, requested_page: int
    ) -> tuple[int, list[int], bool, bool]:
        pagination = soup.select_one("ul.pagination")
        if pagination is None:
            return requested_page, [requested_page], requested_page > 1, False

        available_pages: list[int] = []
        for node in pagination.select("li a, li span"):
            value = self._parse_int(self._text(node))
            if value is not None and value not in available_pages:
                available_pages.append(value)

        active = pagination.select_one("li.active")
        active_page = self._parse_int(self._text(active)) if active else None
        if active_page is None or active_page != requested_page:
            raise self._parse_error("来源分页状态与请求页不一致")
        previous = pagination.select_one("li.prev")
        next_item = pagination.select_one("li.next")
        has_previous = previous is not None and "disabled" not in (previous.get("class") or [])
        has_next = next_item is not None and "disabled" not in (next_item.get("class") or [])
        return active_page, available_pages or [active_page], has_previous, has_next

    def _parse_home_categories(self, soup: BeautifulSoup) -> dict[str, str]:
        result: dict[str, str] = {}
        for link in soup.select('a[href*="/manga-genre/"]'):
            href = str(link.get("href") or "")
            path = urlparse(urljoin(self.base_url, href)).path
            match = CATEGORY_PATH_PATTERN.match(path)
            if not match:
                continue
            slug = match.group(1).lower()
            if SLUG_PATTERN.fullmatch(slug) and slug not in result:
                result[slug] = self._text(link) or self._format_slug(slug)
        return result

    def _parse_sitemap_categories(self, payload: bytes) -> list[str]:
        if payload.startswith(b"\x1f\x8b"):
            try:
                payload = gzip.decompress(payload)
            except OSError as exc:
                raise self._parse_error("分类 sitemap 压缩内容损坏") from exc
        soup = BeautifulSoup(payload, "xml")
        slugs: list[str] = []
        for loc in soup.select("url > loc"):
            path = urlparse(self._text(loc)).path
            match = CATEGORY_PATH_PATTERN.match(path)
            if not match:
                continue
            slug = match.group(1).lower()
            if SLUG_PATTERN.fullmatch(slug) and slug not in slugs:
                slugs.append(slug)
        if not slugs:
            raise self._parse_error("分类 sitemap 中没有有效分类")
        return slugs

    def _parse_detail_metadata(self, soup: BeautifulSoup) -> dict[str, object]:
        result: dict[str, object] = {}
        for item in soup.select(".tab-summary .post-content_item, .post-status .post-content_item"):
            heading = self._text(item.select_one(".summary-heading h5")).rstrip(":").lower()
            content = item.select_one(".summary-content")
            if not heading or content is None:
                continue
            links = [self._text(link) for link in content.select("a") if self._text(link)]
            result[heading] = self._text(content)
            if links:
                result[f"{heading}:links"] = links
        return result

    @staticmethod
    def _metadata_links(metadata: dict[str, object], key: str) -> list[str]:
        value = metadata.get(f"{key}:links", [])
        return list(value) if isinstance(value, list) else []

    @staticmethod
    def _metadata_text(metadata: dict[str, object], key: str) -> str:
        value = metadata.get(key, "")
        return value if isinstance(value, str) else ""

    def _extract_comic_id(self, link: Tag | None) -> str | None:
        if link is None:
            return None
        path = urlparse(urljoin(self.base_url, str(link.get("href") or ""))).path
        match = COMIC_PATH_PATTERN.match(path)
        if not match or not SLUG_PATTERN.fullmatch(match.group(1)):
            return None
        return match.group(1)

    def _extract_chapter_id(self, link: Tag | None, *, expected_comic_id: str) -> str | None:
        if link is None:
            return None
        path = urlparse(urljoin(self.base_url, str(link.get("href") or ""))).path
        match = CHAPTER_PATH_PATTERN.match(path)
        if (
            not match
            or match.group(1) != expected_comic_id
            or not SLUG_PATTERN.fullmatch(match.group(2))
        ):
            return None
        return match.group(2)

    def _image_url(self, image: Tag | None) -> str:
        if image is None:
            return ""
        value = str(image.get("data-src") or image.get("src") or "").strip()
        return urljoin(self.base_url, value) if value else ""

    def _url(self, path: str) -> str:
        return urljoin(f"{self.base_url}/", path.lstrip("/"))

    def _validate_final_url(self, url: str, *, media: bool) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        allowed = host == self.base_host or (media and host.endswith(f".{self.base_host}"))
        if parsed.scheme not in {"http", "https"} or not allowed:
            raise ValueError("source redirected to a disallowed host")

    def _validate_media_url(self, url: str) -> None:
        self._validate_final_url(url, media=True)

    @staticmethod
    def _validate_page(page: int) -> None:
        if page < 1:
            raise AppError("VALIDATION_ERROR", "页码必须大于等于 1", 422, False)

    @staticmethod
    def _validate_order(order: str) -> None:
        if order not in ALLOWED_ORDERS:
            raise AppError("VALIDATION_ERROR", "排序参数无效", 422, False)

    @staticmethod
    def _validate_slug(value: str, label: str) -> None:
        if not SLUG_PATTERN.fullmatch(value):
            raise AppError("VALIDATION_ERROR", f"{label} ID 无效", 422, False)

    @staticmethod
    def _text(node: Tag | None) -> str:
        return node.get_text(" ", strip=True) if node is not None else ""

    @classmethod
    def _optional_text(cls, node: Tag | None) -> str | None:
        value = cls._text(node)
        return value or None

    @staticmethod
    def _parse_float(value: str) -> float | None:
        try:
            return float(value.strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_int(value: str) -> int | None:
        try:
            return int(value.strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _format_slug(slug: str) -> str:
        labels = {"bl": "BL", "gl": "GL", "ntr": "NTR", "rpg": "RPG", "sci-fi": "Sci-Fi"}
        return labels.get(slug, slug.replace("-", " ").title())

    @staticmethod
    def _parse_error(message: str) -> AppError:
        return AppError("UPSTREAM_PARSE_ERROR", message, 502, True)
