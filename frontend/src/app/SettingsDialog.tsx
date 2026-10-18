// Settings (spec §5.1, M11): what Portolan is configured with, read-only, plus the theme.
// Nothing here can show a key: the backend never sends one.

import { useBudget, useSettings } from "../api/hooks";
import { Dialog } from "../design/Dialog";
import { useUI } from "../state/store";
import styles from "./dialogs.module.css";
import type { Theme } from "./TopBar";

export const FREE_TIER_NOTICE =
  "Free Gemini keys may let Google use what you send. Don't analyze private code with a free key.";

export function SettingsDialog({ theme, setTheme }: { theme: Theme; setTheme: (t: Theme) => void }) {
  const close = useUI((s) => s.closeDialog);
  const { data: settings, isLoading } = useSettings();
  const { data: budget } = useBudget();

  // An older backend has no /api/settings: show what /api/budget already knows.
  const provider = settings?.provider ?? budget?.provider ?? null;
  const limits = settings?.limits ?? budget?.limits ?? null;
  const showNotice = settings ? settings.free_tier_notice || settings.provider === "gemini" : provider === "gemini";

  return (
    <Dialog title="Settings" onClose={close}>
      {isLoading ? (
        <p className={styles.note}>Loading settings…</p>
      ) : (
        <dl className={styles.list}>
          <dt>Provider</dt>
          <dd>{provider ?? "unknown"}</dd>
          <dt>Fast model</dt>
          <dd>{settings?.models.fast ?? "not reported"}</dd>
          <dt>Default model</dt>
          <dd>{settings?.models.default ?? "not reported"}</dd>
          <dt>Strong model</dt>
          <dd>{settings?.models.strong ?? "not reported"}</dd>
          <dt>Rate limits</dt>
          <dd>
            {limits
              ? `${limits.rpm} per minute · ${limits.rpd.toLocaleString()} per day · ${limits.tpm.toLocaleString()} tokens per minute`
              : "not reported"}
          </dd>
          <dt>Sandbox</dt>
          <dd>{settings?.sandbox_mode ?? "not reported"}</dd>
          <dt>Version</dt>
          <dd>{settings?.version ?? "not reported"}</dd>
        </dl>
      )}
      {showNotice && <p className={styles.note}>{FREE_TIER_NOTICE}</p>}
      <p className={styles.note}>Edit .env on the machine running Portolan; the key is never shown here.</p>
      <label htmlFor="theme" className={styles.field}>
        Theme
      </label>
      <select
        id="theme"
        className={styles.select}
        value={theme}
        onChange={(e) => setTheme(e.target.value as Theme)}
      >
        <option value="system">Follow system</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </Dialog>
  );
}
