import type { TypeEditorTabComponent } from './types'

/** events-and-sync.md §5/Step 11: `tabId -> component` registry for
 *  plugin-supplied type-editor tabs, mirroring the `udm-editors` field
 *  registry pattern (register a component here per tab id; anything not
 *  registered falls back to the generic JSON editor — see
 *  `JsonTabFallback.tsx`).
 *
 *  No concrete plugin (sync_webhook, sync_caldav, sync_ical, sync_pretix)
 *  has a real tab component yet, so this starts empty — the fallback
 *  mechanism is what Step 11 actually delivers here. Future plugin tabs
 *  register with:
 *
 *      import { typeEditorTabRegistry } from '../type-editor-tabs/registry'
 *      typeEditorTabRegistry['sync_webhook'] = SyncWebhookTab
 */
export const typeEditorTabRegistry: Record<string, TypeEditorTabComponent> = {}
