import { Link } from "@tanstack/react-router";
import { CompassIcon, HeartIcon, HistoryIcon, HomeIcon, SettingsIcon } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const items = [
  { to: "/", label: "首页", icon: HomeIcon },
  { to: "/explore", label: "探索", icon: CompassIcon },
  { to: "/favorites", label: "收藏", icon: HeartIcon },
  { to: "/history", label: "历史", icon: HistoryIcon },
  { to: "/settings", label: "设置", icon: SettingsIcon },
] as const;

export function FloatingNav() {
  return (
    <nav className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-full border border-border/70 bg-background/85 p-1.5 shadow-lg shadow-black/5 backdrop-blur-xl">
      <ul className="flex items-center gap-1">
        {items.map((item) => (
          <li key={item.to}>
            <Link
              to={item.to}
              activeOptions={{ exact: item.to === "/" }}
              aria-label={item.label}
              title={item.label}
              className={cn(buttonVariants({ variant: "ghost", size: "icon" }), "size-11")}
              activeProps={{ className: "bg-primary text-primary-foreground hover:bg-primary/90" }}
            >
              <item.icon className="size-5" />
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
