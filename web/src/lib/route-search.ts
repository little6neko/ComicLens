import type { ComicOrder } from "@/domain/api";

export function positivePage(value: unknown) {
  const page = typeof value === "number" ? value : Number(value);
  return Number.isInteger(page) && page > 0 ? page : 1;
}

export function comicOrder(value: unknown): ComicOrder {
  return value === "rating" || value === "views" ? value : "latest";
}
