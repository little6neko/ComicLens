import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { EyeIcon, EyeOffIcon, LoaderCircleIcon, LockKeyholeIcon } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";

export const Route = createFileRoute("/login")({
  validateSearch: (search: Record<string, unknown>) => ({
    next: safeNext(search.next),
  }),
  component: LoginPage,
});

function LoginPage() {
  const { next } = Route.useSearch();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const login = useMutation({
    mutationFn: api.login,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.auth });
      window.location.replace(next);
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (password) login.mutate(password);
  }

  return (
    <main className="grid min-h-dvh place-items-center bg-muted/50 px-4 py-10">
      <section className="w-full max-w-sm rounded-[2rem] border bg-card p-7 text-card-foreground shadow-xl shadow-black/5">
        <div className="flex size-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
          <LockKeyholeIcon className="size-5" />
        </div>
        <h1 className="mt-6 text-3xl font-bold tracking-tight">访问 ComicLens</h1>
        <p className="mt-2 text-sm text-muted-foreground">输入服务器环境变量配置的访问密码。</p>

        <form onSubmit={submit} className="mt-7 space-y-4">
          <label className="block">
            <span className="mb-2 block text-sm font-medium">密码</span>
            <span className="relative block">
              <Input
                autoFocus
                autoComplete="current-password"
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                aria-invalid={login.isError}
                className="pr-11"
              />
              <button
                type="button"
                onClick={() => setShowPassword((value) => !value)}
                className="absolute top-1/2 right-3 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                aria-label={showPassword ? "隐藏密码" : "显示密码"}
              >
                {showPassword ? <EyeOffIcon className="size-4" /> : <EyeIcon className="size-4" />}
              </button>
            </span>
          </label>
          {login.isError && (
            <p className="rounded-xl bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {login.error instanceof Error ? login.error.message : "登录失败"}
            </p>
          )}
          <Button
            type="submit"
            size="lg"
            className="w-full"
            disabled={!password || login.isPending}
          >
            {login.isPending && <LoaderCircleIcon className="size-4 animate-spin" />}
            登录
          </Button>
        </form>
      </section>
    </main>
  );
}

function safeNext(value: unknown) {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return "/";
  return value;
}
