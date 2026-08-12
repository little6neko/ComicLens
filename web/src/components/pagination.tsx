import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function Pagination({
  page,
  availablePages,
  hasPrevious,
  hasNext,
  onPage,
}: {
  page: number;
  availablePages: number[];
  hasPrevious: boolean;
  hasNext: boolean;
  onPage: (page: number) => void;
}) {
  return (
    <nav className="flex flex-wrap items-center justify-center gap-2 pt-3" aria-label="分页">
      <Button
        variant="outline"
        size="icon"
        disabled={!hasPrevious}
        onClick={() => onPage(page - 1)}
        aria-label="上一页"
      >
        <ChevronLeftIcon className="size-4" />
      </Button>
      {availablePages.map((value) => (
        <button
          type="button"
          key={value}
          onClick={() => onPage(value)}
          className={cn(
            "size-9 rounded-lg text-sm font-medium transition-colors",
            value === page ? "bg-primary text-primary-foreground" : "hover:bg-muted",
          )}
        >
          {value}
        </button>
      ))}
      <Button
        variant="outline"
        size="icon"
        disabled={!hasNext}
        onClick={() => onPage(page + 1)}
        aria-label="下一页"
      >
        <ChevronRightIcon className="size-4" />
      </Button>
    </nav>
  );
}
