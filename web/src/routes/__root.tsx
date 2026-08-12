import { createRootRoute, Outlet } from "@tanstack/react-router";

export const Route = createRootRoute({
  component: () => <Outlet />,
  notFoundComponent: () => (
    <main className="flex min-h-dvh items-center justify-center bg-muted px-4">
      <div className="text-center">
        <p className="font-mono text-7xl font-bold">404</p>
        <p className="mt-3 text-muted-foreground">页面不存在</p>
      </div>
    </main>
  ),
});
