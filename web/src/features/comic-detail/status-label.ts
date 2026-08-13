const STATUS_LABELS: Record<string, string> = {
  ongoing: "连载",
  completed: "完结",
};

export function getComicStatusLabel(status: string | null | undefined): string | null {
  const sourceLabel = status?.trim();
  if (!sourceLabel) return null;
  return STATUS_LABELS[sourceLabel.toLowerCase()] ?? sourceLabel;
}
