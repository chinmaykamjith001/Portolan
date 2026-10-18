// Selection on a canvas node must not rely on the outline alone (spec §13.12). React Flow owns
// the focusable wrapper (role "group", aria-label), and rebuilding every node's label on each
// selection would cost pan performance, so the selected one gets `aria-current` instead.

import { useEffect, useRef } from "react";

export function useAriaCurrent<T extends HTMLElement>(on: boolean) {
  const ref = useRef<T>(null);
  useEffect(() => {
    const wrapper = ref.current?.closest(".react-flow__node");
    if (!wrapper) return;
    if (on) wrapper.setAttribute("aria-current", "true");
    else wrapper.removeAttribute("aria-current");
    return () => wrapper.removeAttribute("aria-current");
  }, [on]);
  return ref;
}
