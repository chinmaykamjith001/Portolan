// The shortcut list (spec §13.10). Every row here must be a shortcut that actually works.

import { Dialog } from "../design/Dialog";
import { useUI } from "../state/store";
import styles from "./dialogs.module.css";

interface Row {
  keys: string[];
  does: string;
}

export const SHORTCUTS: Row[] = [
  { keys: ["Esc"], does: "Close the popover, then the drawer, then go back one level" },
  { keys: ["Enter", "Space"], does: "Jump into the selected concept or stop" },
  { keys: ["←", "→"], does: "Previous or next stop on the Route" },
  { keys: ["Tab", "Shift+Tab"], does: "Move the selection between concepts or stops" },
  { keys: ["/"], does: "Search this paper's concepts (on the FAQ: ask a question)" },
  { keys: ["f"], does: "Fit the view" },
  { keys: ["?"], does: "Show this list" },
];

export function ShortcutHelp() {
  const close = useUI((s) => s.closeDialog);
  return (
    <Dialog title="Keyboard shortcuts" onClose={close}>
      <dl className={styles.keys}>
        {SHORTCUTS.map((r) => (
          <div key={r.keys.join("+")} style={{ display: "contents" }}>
            <dt>
              {r.keys.map((k) => (
                <kbd key={k} className={styles.kbd}>
                  {k}
                </kbd>
              ))}
            </dt>
            <dd>{r.does}</dd>
          </div>
        ))}
      </dl>
    </Dialog>
  );
}
