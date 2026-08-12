import { Link } from "@tanstack/react-router";
import { StarIcon } from "lucide-react";

import type { ComicSummary, FeaturedComic } from "@/domain/api";
import { cn } from "@/lib/utils";

export function ComicCard({ comic, className }: { comic: ComicSummary; className?: string }) {
  const latest = comic.latestChapters[0];
  return (
    <Link
      to="/comic/$comicId"
      params={{ comicId: comic.comicId }}
      className={cn("group min-w-0", className)}
    >
      <div className="relative aspect-[3/4] overflow-hidden rounded-2xl bg-muted shadow-sm">
        <img
          src={comic.coverUrl}
          alt={comic.title}
          loading="lazy"
          className="size-full object-cover transition duration-300 group-hover:scale-[1.03]"
        />
        {comic.isAdult && (
          <span className="absolute top-2 left-2 rounded-full bg-black/70 px-2 py-1 text-[10px] font-semibold text-white backdrop-blur">
            18+
          </span>
        )}
        {comic.rating !== null && (
          <span className="absolute right-2 bottom-2 flex items-center gap-1 rounded-full bg-black/70 px-2 py-1 text-[10px] font-medium text-white backdrop-blur">
            <StarIcon className="size-3 fill-current" /> {comic.rating.toFixed(1)}
          </span>
        )}
      </div>
      <h3 className="mt-2 line-clamp-2 text-sm font-semibold leading-snug">{comic.title}</h3>
      {latest && <p className="mt-1 truncate text-xs text-muted-foreground">{latest.title}</p>}
    </Link>
  );
}

export function FeaturedCard({ comic }: { comic: FeaturedComic }) {
  return (
    <Link
      to="/comic/$comicId"
      params={{ comicId: comic.comicId }}
      className="group relative block w-40 shrink-0 sm:w-48"
    >
      <div className="aspect-[3/4] overflow-hidden rounded-3xl bg-muted shadow-md">
        <img
          src={comic.coverUrl}
          alt={comic.title}
          loading="lazy"
          className="size-full object-cover transition duration-300 group-hover:scale-[1.04]"
        />
      </div>
      <h3 className="mt-3 line-clamp-2 font-semibold leading-snug">{comic.title}</h3>
      {comic.chapterLabel && (
        <p className="mt-1 truncate text-xs text-muted-foreground">{comic.chapterLabel}</p>
      )}
    </Link>
  );
}

export function ComicGrid({ comics }: { comics: ComicSummary[] }) {
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
      {comics.map((comic) => (
        <ComicCard key={comic.comicId} comic={comic} />
      ))}
    </div>
  );
}
