import { Slider as SliderPrimitive } from "radix-ui";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

export function Slider({ className, ...props }: ComponentProps<typeof SliderPrimitive.Root>) {
  return (
    <SliderPrimitive.Root
      className={cn(
        "relative flex h-5 w-full touch-none items-center select-none data-[disabled]:opacity-45",
        className,
      )}
      {...props}
    >
      <SliderPrimitive.Track className="relative h-1 w-full grow overflow-hidden rounded-full bg-white/20">
        <SliderPrimitive.Range className="absolute h-full bg-white" />
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb className="block size-3 rounded-full border-2 border-white bg-zinc-950 shadow-md outline-none transition-transform hover:scale-125 focus-visible:ring-2 focus-visible:ring-white/60" />
    </SliderPrimitive.Root>
  );
}
