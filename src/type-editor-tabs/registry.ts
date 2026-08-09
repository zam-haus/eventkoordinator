import type { TypeEditorTabComponent } from './types'
import { SyncTargetsTab } from './SyncTargetsTab'

/** events-and-sync.md §5/Step 11: `tabId -> component` registry for
 *  plugin-supplied type-editor tabs, mirroring the `udm-editors` field
 *  registry pattern (register a component here per tab id; anything not
 *  registered falls back to the generic JSON editor — see
 *  `JsonTabFallback.tsx`).
 *
 *  Future plugin tabs (sync_webhook, sync_caldav, sync_ical, sync_pretix)
 *  register the same way:
 *
 *      import { typeEditorTabRegistry } from '../type-editor-tabs/registry'
 *      typeEditorTabRegistry['sync_webhook'] = SyncWebhookTab
 */
export const typeEditorTabRegistry: Record<string, TypeEditorTabComponent> = {
  sync_targets: SyncTargetsTab,
}
