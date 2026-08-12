import { createFileRoute, Outlet } from "@tanstack/react-router";

import { FloatingNav } from "@/components/floating-nav";

export const Route = createFileRoute("/_app")({
  component: AppLayout,
});

function AppLayout() {
  return (
    <div className="relative min-h-dvh">
      <FloatingNav />
      <Outlet />
    </div>
  );
}
