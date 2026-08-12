import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  BookOpenIcon,
  DatabaseIcon,
  KeyRoundIcon,
  LanguagesIcon,
  LogOutIcon,
  SaveIcon,
  ServerCogIcon,
  SettingsIcon,
  ShieldAlertIcon,
  SlidersHorizontalIcon,
  Trash2Icon,
} from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { toast } from "sonner";

import { AppPage } from "@/components/app-page";
import { ErrorState, LoadingState } from "@/components/query-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import type {
  SensitiveAction,
  SensitiveSettingState,
  ServerSettings,
  SettingsPatch,
} from "@/domain/api";
import { api } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

type Draft = Omit<
  ServerSettings,
  | "targetLanguage"
  | "accessPasswordEnabled"
  | "publicListenerWarning"
  | "ocrApiUrl"
  | "ocrToken"
  | "deeplApiKey"
  | "deeplxUrl"
  | "fallbackProxyUrl"
>;

type SecretKey = "ocrApiUrl" | "ocrToken" | "deeplApiKey" | "deeplxUrl" | "fallbackProxyUrl";

interface SecretDraft {
  action: "keep" | "replace" | "clear";
  value: string;
}

const secretKeys: SecretKey[] = [
  "ocrApiUrl",
  "ocrToken",
  "deeplApiKey",
  "deeplxUrl",
  "fallbackProxyUrl",
];

export const Route = createFileRoute("/_app/settings")({ component: SettingsPage });

function SettingsPage() {
  const queryClient = useQueryClient();
  const { setTheme } = useTheme();
  const settings = useQuery({ queryKey: queryKeys.settings, queryFn: api.settings });
  const cache = useQuery({ queryKey: queryKeys.cache, queryFn: api.cacheStats });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [secrets, setSecrets] = useState<Record<SecretKey, SecretDraft>>(emptySecrets);

  useEffect(() => {
    if (!settings.data) return;
    setDraft(toDraft(settings.data));
  }, [settings.data]);

  const save = useMutation({
    mutationFn: api.patchSettings,
    onSuccess: async (next) => {
      queryClient.setQueryData(queryKeys.settings, next);
      setDraft(toDraft(next));
      setSecrets(emptySecrets());
      setTheme(next.theme);
      await queryClient.invalidateQueries({ queryKey: queryKeys.cache });
      toast.success("设置已保存到服务器");
    },
    onError: showError,
  });
  const clearCache = useMutation({
    mutationFn: api.clearCache,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.cache });
      toast.success("普通缓存已清除");
    },
    onError: showError,
  });
  const logout = useMutation({
    mutationFn: api.logout,
    onSuccess: () => window.location.replace("/login"),
    onError: showError,
  });

  function patch<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!draft) return;
    const patch: SettingsPatch = { ...draft };
    for (const key of secretKeys) patch[key] = secretAction(secrets[key]);
    save.mutate(patch);
  }

  if (settings.isPending || !draft) {
    return (
      <AppPage>
        <LoadingState label="正在读取服务器设置…" />
      </AppPage>
    );
  }
  if (settings.isError) {
    return (
      <AppPage>
        <ErrorState error={settings.error} retry={() => void settings.refetch()} />
      </AppPage>
    );
  }

  return (
    <AppPage>
      <header>
        <div className="mb-3 flex size-11 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
          <SettingsIcon className="size-5" />
        </div>
        <h1 className="text-4xl font-bold tracking-tight">设置</h1>
        <p className="mt-2 text-muted-foreground">阅读、翻译接口和缓存配置都保存在服务器。</p>
      </header>

      {settings.data.publicListenerWarning && (
        <div className="flex items-start gap-3 rounded-3xl border border-amber-500/30 bg-amber-500/10 p-5 text-amber-950 dark:text-amber-100">
          <ShieldAlertIcon className="mt-0.5 size-5 shrink-0" />
          <div>
            <p className="font-semibold">当前公开监听且未设置访问密码</p>
            <p className="mt-1 text-sm opacity-80">
              ComicLens 正监听非回环地址。请通过 COMICLENS_ACCESS_PASSWORD 环境变量启用门禁。
            </p>
          </div>
        </div>
      )}

      <form onSubmit={submit} className="space-y-6">
        <SettingsSection icon={<BookOpenIcon />} title="阅读">
          <Field label="主题">
            <Select
              value={draft.theme}
              onChange={(value) => patch("theme", value as Draft["theme"])}
              options={[
                ["system", "跟随系统"],
                ["light", "浅色"],
                ["dark", "深色"],
              ]}
            />
          </Field>
          <Field label="默认阅读模式">
            <Select
              value={draft.readingMode}
              onChange={(value) => patch("readingMode", value as Draft["readingMode"])}
              options={[
                ["strip", "条漫"],
                ["page", "单页"],
                ["double", "双页"],
              ]}
            />
          </Field>
          <Field label="翻页方向">
            <Select
              value={draft.pageDirection}
              onChange={(value) => patch("pageDirection", value as Draft["pageDirection"])}
              options={[
                ["ltr", "从左到右"],
                ["rtl", "从右到左"],
              ]}
            />
          </Field>
          <ToggleField
            label="进入章节时默认实时翻译"
            description="阅读器内的开关只覆盖当次会话。"
            checked={draft.realtimeTranslationDefault}
            onCheckedChange={(value) => patch("realtimeTranslationDefault", value)}
          />
        </SettingsSection>

        <SettingsSection icon={<LanguagesIcon />} title="OCR 与翻译">
          <Field label="源语言" hint="目标语言固定为简体中文（ZH-HANS）">
            <Select
              value={draft.sourceLanguage}
              onChange={(value) => patch("sourceLanguage", value as Draft["sourceLanguage"])}
              options={[
                ["AUTO", "自动识别（默认）"],
                ["EN", "英语"],
                ["KO", "韩语"],
              ]}
            />
          </Field>
          <SecretField
            label="OCR 异步任务 URL"
            state={settings.data.ocrApiUrl}
            draft={secrets.ocrApiUrl}
            onChange={(value) => setSecret(setSecrets, "ocrApiUrl", value)}
            placeholder="https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
          />
          <SecretField
            label="OCR Token"
            state={settings.data.ocrToken}
            draft={secrets.ocrToken}
            onChange={(value) => setSecret(setSecrets, "ocrToken", value)}
            type="password"
          />
          <Field label="OCR 模型">
            <Input
              value={draft.ocrModel}
              onChange={(event) => patch("ocrModel", event.target.value)}
              maxLength={200}
              placeholder="PaddleOCR-VL-1.6"
              required
            />
          </Field>
          <NumberField
            label="OCR 轮询间隔（秒）"
            value={draft.ocrPollIntervalSeconds}
            min={0.2}
            max={60}
            step={0.1}
            onChange={(value) => patch("ocrPollIntervalSeconds", value)}
          />
          <NumberField
            label="OCR 总超时（秒）"
            value={draft.ocrTimeoutSeconds}
            min={1}
            max={3600}
            onChange={(value) => patch("ocrTimeoutSeconds", value)}
          />
          <NumberField
            label="OCR 并发"
            hint="分片渐进翻译固定按顺序单片执行；此值仅为旧任务兼容保留。"
            value={draft.ocrConcurrency}
            min={1}
            max={16}
            onChange={(value) => patch("ocrConcurrency", value)}
          />
          <Field label="翻译服务" hint="请求失败时不会自动切换到另一服务。">
            <Select
              value={draft.translationService}
              onChange={(value) =>
                patch("translationService", value as Draft["translationService"])
              }
              options={[
                ["deepl", "DeepL 官方 API（默认）"],
                ["deeplx", "DeepLX"],
              ]}
            />
          </Field>
          {draft.translationService === "deepl" ? (
            <SecretField
              label="DeepL API Key"
              state={settings.data.deeplApiKey}
              draft={secrets.deeplApiKey}
              onChange={(value) => setSecret(setSecrets, "deeplApiKey", value)}
              hint="以 :fx 结尾的 Key 自动使用 Free API，否则使用 Pro API。"
              type="password"
            />
          ) : (
            <SecretField
              label="DeepLX URL"
              state={settings.data.deeplxUrl}
              draft={secrets.deeplxUrl}
              onChange={(value) => setSecret(setSecrets, "deeplxUrl", value)}
              placeholder="https://deeplx.example.com/translate"
            />
          )}
          <NumberField
            label="翻译超时（秒）"
            value={draft.translationTimeoutSeconds}
            min={1}
            max={600}
            onChange={(value) => patch("translationTimeoutSeconds", value)}
          />
          <NumberField
            label="翻译并发"
            hint="只用于当前分片内部的文本请求；不同分片不会并发或乱序显示。"
            value={draft.translationConcurrency}
            min={1}
            max={16}
            onChange={(value) => patch("translationConcurrency", value)}
          />
          <SecretField
            label="回退代理 URL"
            state={settings.data.fallbackProxyUrl}
            draft={secrets.fallbackProxyUrl}
            onChange={(value) => setSecret(setSecrets, "fallbackProxyUrl", value)}
            placeholder="http://user:password@proxy:8080"
          />
        </SettingsSection>

        <SettingsSection icon={<SlidersHorizontalIcon />} title="长图高级设置" defaultOpen={false}>
          <NumberField
            label="长图阈值（px）"
            value={draft.longImageThreshold}
            min={1000}
            max={100000}
            onChange={(value) => patch("longImageThreshold", value)}
          />
          <NumberField
            label="OCR 分片高度（px）"
            value={draft.ocrSliceHeight}
            min={500}
            max={50000}
            onChange={(value) => patch("ocrSliceHeight", value)}
          />
          <NumberField
            label="OCR 分片重叠（px）"
            value={draft.ocrSliceOverlap}
            min={0}
            max={5000}
            onChange={(value) => patch("ocrSliceOverlap", value)}
          />
          <NumberField
            label="阅读分片高度（px）"
            hint="仅兼容旧整页译图任务；新任务直接按 OCR 分片逐片显示。"
            value={draft.readingSliceHeight}
            min={500}
            max={50000}
            onChange={(value) => patch("readingSliceHeight", value)}
          />
        </SettingsSection>

        <SettingsSection icon={<DatabaseIcon />} title="缓存">
          <NumberField
            label="缓存上限（MB）"
            hint="默认 5120 MB；原图、OCR 和译图没有时间 TTL，仅超限时按 LRU 淘汰。"
            value={draft.cacheMaxMb}
            min={128}
            max={102400}
            onChange={(value) => patch("cacheMaxMb", value)}
          />
          {cache.isPending ? (
            <p className="text-sm text-muted-foreground">正在统计缓存…</p>
          ) : cache.isError ? (
            <p className="text-sm text-destructive">缓存统计读取失败</p>
          ) : (
            <div className="col-span-full rounded-2xl bg-muted p-4">
              <div className="flex items-center justify-between gap-4 text-sm">
                <span>已使用 {formatBytes(cache.data.usedBytes)}</span>
                <span className="text-muted-foreground">
                  上限 {formatBytes(cache.data.maxBytes)}
                </span>
              </div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-background">
                <div
                  className={cn(
                    "h-full rounded-full bg-primary",
                    cache.data.overLimit && "bg-destructive",
                  )}
                  style={{
                    width: `${Math.min(100, (cache.data.usedBytes / Math.max(1, cache.data.maxBytes)) * 100)}%`,
                  }}
                />
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                {cache.data.bundleCount} 个缓存包 · {cache.data.entryCount} 个文件
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="mt-4"
                disabled={clearCache.isPending}
                onClick={() => {
                  if (window.confirm("清除全部普通缓存？收藏、历史、设置和已读状态不会删除。")) {
                    clearCache.mutate();
                  }
                }}
              >
                <Trash2Icon className="size-3.5" /> 清除缓存
              </Button>
            </div>
          )}
        </SettingsSection>

        <SettingsSection icon={<ServerCogIcon />} title="服务器">
          <div className="col-span-full flex flex-wrap items-center justify-between gap-4 rounded-2xl bg-muted p-4">
            <div>
              <p className="font-medium">访问密码</p>
              <p className="mt-1 text-sm text-muted-foreground">
                {settings.data.accessPasswordEnabled
                  ? "已通过 COMICLENS_ACCESS_PASSWORD 启用"
                  : "未启用；空环境变量不会显示登录界面"}
              </p>
            </div>
            {settings.data.accessPasswordEnabled && (
              <Button
                type="button"
                variant="outline"
                disabled={logout.isPending}
                onClick={() => logout.mutate()}
              >
                <LogOutIcon className="size-4" /> 退出登录
              </Button>
            )}
          </div>
        </SettingsSection>

        <div className="flex justify-end">
          <Button
            type="submit"
            size="lg"
            disabled={save.isPending}
            className="shadow-xl shadow-black/10"
          >
            <SaveIcon className="size-4" /> {save.isPending ? "保存中…" : "保存全部设置"}
          </Button>
        </div>
      </form>
    </AppPage>
  );
}

function SettingsSection({
  icon,
  title,
  children,
  defaultOpen = true,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details open={defaultOpen} className="group rounded-3xl border bg-card shadow-sm">
      <summary className="flex cursor-pointer list-none items-center gap-3 p-5 font-semibold [&::-webkit-details-marker]:hidden">
        <span className="flex size-9 items-center justify-center rounded-xl bg-muted [&>svg]:size-4">
          {icon}
        </span>
        {title}
        <span className="ml-auto text-xs font-normal text-muted-foreground group-open:hidden">
          展开
        </span>
      </summary>
      <div className="grid gap-5 border-t p-5 sm:grid-cols-2">{children}</div>
    </details>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block min-w-0">
      <span className="mb-2 block text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="mt-1.5 block text-xs leading-5 text-muted-foreground">{hint}</span>}
    </label>
  );
}

function ToggleField({
  label,
  description,
  checked,
  onCheckedChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-2xl bg-muted p-4 sm:col-span-2">
      <div>
        <p className="font-medium">{label}</p>
        <p className="mt-1 text-sm text-muted-foreground">{description}</p>
      </div>
      <Switch checked={checked} onCheckedChange={onCheckedChange} aria-label={label} />
    </div>
  );
}

function Select({
  value,
  options,
  onChange,
}: {
  value: string;
  options: [string, string][];
  onChange: (value: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="h-11 w-full rounded-xl border bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
    >
      {options.map(([option, label]) => (
        <option key={option} value={option}>
          {label}
        </option>
      ))}
    </select>
  );
}

function NumberField({
  label,
  hint,
  value,
  min,
  max,
  step = 1,
  onChange,
}: {
  label: string;
  hint?: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
}) {
  return (
    <Field label={label} hint={hint}>
      <Input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        required
        onChange={(event) => {
          const next = Number(event.target.value);
          if (Number.isFinite(next)) onChange(next);
        }}
      />
    </Field>
  );
}

function SecretField({
  label,
  state,
  draft,
  onChange,
  placeholder,
  hint,
  type = "text",
}: {
  label: string;
  state: SensitiveSettingState;
  draft: SecretDraft;
  onChange: (draft: SecretDraft) => void;
  placeholder?: string;
  hint?: string;
  type?: "text" | "password";
}) {
  return (
    <div className="min-w-0">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{label}</span>
        <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
          <KeyRoundIcon className="size-3" /> {state.configured ? state.masked : "未配置"}
        </span>
      </div>
      <div className="flex gap-2">
        <select
          value={draft.action}
          onChange={(event) =>
            onChange({ action: event.target.value as SecretDraft["action"], value: "" })
          }
          className="h-11 w-24 shrink-0 rounded-xl border bg-background px-2 text-xs outline-none focus:ring-2 focus:ring-ring"
          aria-label={`${label}操作`}
        >
          <option value="keep">保留</option>
          <option value="replace">替换</option>
          <option value="clear">清除</option>
        </select>
        <Input
          type={type}
          value={draft.value}
          disabled={draft.action !== "replace"}
          required={draft.action === "replace"}
          onChange={(event) => onChange({ ...draft, value: event.target.value })}
          placeholder={draft.action === "replace" ? placeholder : "不会读取或修改明文"}
          autoComplete="off"
        />
      </div>
      {hint && <span className="mt-1.5 block text-xs leading-5 text-muted-foreground">{hint}</span>}
    </div>
  );
}

function toDraft(settings: ServerSettings): Draft {
  const {
    targetLanguage: _targetLanguage,
    accessPasswordEnabled: _accessPasswordEnabled,
    publicListenerWarning: _publicListenerWarning,
    ocrApiUrl: _ocrApiUrl,
    ocrToken: _ocrToken,
    deeplApiKey: _deeplApiKey,
    deeplxUrl: _deeplxUrl,
    fallbackProxyUrl: _fallbackProxyUrl,
    ...draft
  } = settings;
  return draft;
}

function emptySecrets(): Record<SecretKey, SecretDraft> {
  return Object.fromEntries(
    secretKeys.map((key) => [key, { action: "keep", value: "" }]),
  ) as Record<SecretKey, SecretDraft>;
}

function setSecret(
  setter: React.Dispatch<React.SetStateAction<Record<SecretKey, SecretDraft>>>,
  key: SecretKey,
  value: SecretDraft,
) {
  setter((current) => ({ ...current, [key]: value }));
}

function secretAction(value: SecretDraft): SensitiveAction {
  if (value.action === "replace") return { action: "replace", value: value.value };
  return { action: value.action };
}

function formatBytes(value: number) {
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function showError(error: unknown) {
  toast.error(error instanceof Error ? error.message : "操作失败");
}
