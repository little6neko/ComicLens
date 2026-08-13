import { CheckIcon, ChevronDownIcon } from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

export type SelectOption = readonly [value: string, label: string];

export function Select({
  value,
  options,
  onValueChange,
  ariaLabel,
  className,
  disabled = false,
}: {
  value: string;
  options: readonly SelectOption[];
  onValueChange: (value: string) => void;
  ariaLabel: string;
  className?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const selectedLabel = options.find(([option]) => option === value)?.[1] ?? value;

  useEffect(() => {
    if (!open) return;

    function closeOnOutsideClick(event: MouseEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (triggerRef.current?.contains(target) || contentRef.current?.contains(target)) return;
      setOpen(false);
    }

    document.addEventListener("click", closeOnOutsideClick, true);
    return () => document.removeEventListener("click", closeOnOutsideClick, true);
  }, [open]);

  return (
    <DropdownMenu.Root open={open} onOpenChange={setOpen} modal={false}>
      <DropdownMenu.Trigger
        ref={triggerRef}
        disabled={disabled}
        aria-label={ariaLabel}
        className={cn(
          "group flex h-11 w-full items-center gap-3 rounded-2xl border bg-background px-4 text-left text-sm outline-none transition-[background-color,box-shadow] hover:bg-accent/50 focus-visible:ring-2 focus-visible:ring-ring data-[state=open]:ring-2 data-[state=open]:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
          className,
        )}
      >
        <span className="min-w-0 flex-1 truncate">{selectedLabel}</span>
        <ChevronDownIcon className="settings-select-trigger-icon ml-auto size-4 shrink-0 text-muted-foreground transition-transform duration-150 group-data-[state=open]:rotate-180" />
      </DropdownMenu.Trigger>

      <DropdownMenu.Portal>
        <DropdownMenu.Content
          ref={contentRef}
          side="bottom"
          align="start"
          sideOffset={6}
          collisionPadding={8}
          hideWhenDetached
          loop
          onPointerDownOutside={(event) => {
            if (event.detail.originalEvent.pointerType === "touch") event.preventDefault();
          }}
          className="settings-select-content z-[100] max-h-[var(--radix-dropdown-menu-content-available-height)] w-[var(--radix-dropdown-menu-trigger-width)] overflow-y-auto rounded-2xl border bg-card p-1.5 text-card-foreground shadow-xl shadow-black/10 outline-none"
        >
          <DropdownMenu.RadioGroup
            value={value}
            onValueChange={(nextValue) => {
              if (nextValue !== value) onValueChange(nextValue);
            }}
          >
            {options.map(([option, label]) => (
              <DropdownMenu.RadioItem
                key={option}
                value={option}
                className="relative flex min-h-10 cursor-default select-none items-center rounded-xl py-2 pr-10 pl-3 text-sm outline-none transition-colors data-[disabled]:pointer-events-none data-[disabled]:opacity-50 data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground data-[state=checked]:bg-accent data-[state=checked]:font-medium data-[state=checked]:text-accent-foreground"
              >
                {label}
                <DropdownMenu.ItemIndicator className="absolute right-3 flex items-center justify-center">
                  <CheckIcon className="size-4" />
                </DropdownMenu.ItemIndicator>
              </DropdownMenu.RadioItem>
            ))}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
