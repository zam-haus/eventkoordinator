import type { TypeEditorTabComponent } from './types'
import { SyncTargetsTab } from './SyncTargetsTab'
import { BindingsTab } from './BindingsTab'
import { PretixBindingsTab } from './PretixBindingsTab'

/** events-and-sync.md §5/Step 11: `tabId -> component` registry for
 *  plugin-supplied type-editor tabs, mirroring the `udm-editors` field
 *  registry pattern (register a component here per tab id; anything not
 *  registered falls back to the generic JSON editor — see
 *  `JsonTabFallback.tsx`).
 *
 *  §13.2: sync_caldav/sync_ical register a `{"bindings": {...}}`-shaped tab
 *  config (remote_property -> source), so they share `BindingsTab`.
 *  sync_pretix's config diverges (adds `parent_event`/`items` on top of
 *  `bindings`, §14) so it gets its own `PretixBindingsTab`. sync_webhook
 *  registers no tab at all (payload is always effective JSON, per §5.1).
 */
export const typeEditorTabRegistry: Record<string, TypeEditorTabComponent> = {
  sync_targets: SyncTargetsTab,
  sync_caldav: BindingsTab,
  sync_ical: BindingsTab,
  sync_pretix: PretixBindingsTab,
}
