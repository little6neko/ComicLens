import { useQuery } from "@tanstack/react-query";
import { LoaderCircleIcon } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, type ReactNode } from "react";

import { api } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";

export function AuthBoundary({ children }: { children: ReactNode }) {
  const path = window.location.pathname;
  const { setTheme } = useTheme();
  const session = useQuery({
    queryKey: queryKeys.auth,
    queryFn: api.authSession,
    staleTime: 30_000,
    retry: false,
  });
  const canLoadSettings = !!session.data?.authenticated && path !== "/login";
  const settings = useQuery({
    queryKey: queryKeys.settings,
    queryFn: api.settings,
    enabled: canLoadSettings,
    staleTime: 60_000,
  });

  useEffect(() => {
    if (!session.data) return;
    if (session.data.enabled && !session.data.authenticated && path !== "/login") {
      const next = `${window.location.pathname}${window.location.search}`;
      window.location.replace(`/login?next=${encodeURIComponent(next)}`);
    } else if ((!session.data.enabled || session.data.authenticated) && path === "/login") {
      window.location.replace("/");
    }
  }, [path, session.data]);

  useEffect(() => {
    if (settings.data?.theme) setTheme(settings.data.theme);
  }, [setTheme, settings.data?.theme]);

  const allowed =
    session.data &&
    ((!session.data.enabled && path !== "/login") ||
      (session.data.enabled && (session.data.authenticated || path === "/login")));

  if (!allowed) {
    return (
      <main className="grid min-h-dvh place-items-center bg-background">
        <LoaderCircleIcon
          className="size-6 animate-spin text-muted-foreground"
          aria-label="载入中"
        />
      </main>
    );
  }
  return children;
}
