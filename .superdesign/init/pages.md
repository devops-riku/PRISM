# PRISM page dependency trees

## Conventions

- These trees include local `frontend/src` imports recursively. React, Headless UI, Supabase, and other `node_modules` imports are omitted.
- `frontend/src/App.tsx` is a monolithic route composer whose static imports include every authenticated screen. Expanding that bundler graph under every route would make each page appear to depend visually on every other page. Each tree therefore records the route-specific rendered roots plus the shared shell, while `routes.md` records the complete dispatch logic.
- Repeated transitive groups are expanded once under **Shared dependency closures** and referenced by name in page trees. This is still the complete local import closure, without duplicating the same API/auth/type chain dozens of times.
- All pages also receive global styling from `frontend/src/index.css`; consult `theme.md` for its token summary and source.

## Shared dependency closures

### API closure

```text
- frontend/src/lib/api.ts
  - frontend/src/lib/currencies.ts
    - frontend/src/types.ts
  - frontend/src/lib/auth.ts
    - frontend/src/types.ts
    - frontend/src/lib/workspace.ts
  - frontend/src/lib/workspace.ts
  - frontend/src/types.ts
```

### Role closure

```text
- frontend/src/lib/role.ts
  - frontend/src/lib/api.ts [API closure]
  - frontend/src/types.ts
```

### Job-watch closure

```text
- frontend/src/lib/useJobWatch.ts
  - frontend/src/lib/api.ts [API closure]
  - frontend/src/lib/notifications.ts
    - frontend/src/lib/api.ts [API closure]
    - frontend/src/lib/auth.ts
      - frontend/src/types.ts
      - frontend/src/lib/workspace.ts
    - frontend/src/types.ts
  - frontend/src/types.ts
```

### Authenticated app header closure

Every authenticated page below uses this shell unless its section says otherwise.

```text
- frontend/src/components/AppHeader.tsx
  - frontend/src/lib/auth.ts
    - frontend/src/types.ts
    - frontend/src/lib/workspace.ts
  - frontend/src/lib/navigation.ts
  - frontend/src/lib/theme.ts
  - frontend/src/components/CommandBar.tsx
    - frontend/src/lib/api.ts [API closure]
    - frontend/src/lib/role.ts [Role closure]
    - frontend/src/lib/format.ts
    - frontend/src/types.ts
  - frontend/src/components/NotificationBell.tsx
    - frontend/src/lib/notifications.ts
      - frontend/src/lib/api.ts [API closure]
      - frontend/src/lib/auth.ts
        - frontend/src/types.ts
        - frontend/src/lib/workspace.ts
      - frontend/src/types.ts
    - frontend/src/lib/format.ts
    - frontend/src/components/tokens.ts
  - frontend/src/components/WorkspaceMenu.tsx
    - frontend/src/lib/api.ts [API closure]
    - frontend/src/types.ts
  - frontend/src/components/tokens.ts
```

## 1. `/` — Public landing and authentication

Entry: `frontend/src/components/LandingScreen.tsx`

Current composition: `frontend/src/components/AuthGate.tsx`, reached from the non-client branch in `frontend/src/main.tsx` when authentication is required and no session exists.

Requested composition: an empty-hash branch in `frontend/src/main.tsx` renders this public surface directly at `/` for every visitor. The `/#/` studio branch remains behind `AuthGate` and owns sign-in/sign-up. The redesign changes the diagram input label to **Your Scope** and makes authentication a landing action rather than the dominant embedded card.

Dependencies:

```text
- frontend/src/components/LandingScreen.tsx
  - frontend/src/components/PrismMark.tsx
  - frontend/src/components/tokens.ts
```

Gate/entry dependencies relevant to this page:

```text
- frontend/src/components/AuthGate.tsx
  - frontend/src/lib/auth.ts
    - frontend/src/types.ts
    - frontend/src/lib/workspace.ts
  - frontend/src/components/LandingScreen.tsx [expanded above]
- frontend/src/main.tsx
  - frontend/src/index.css
  - frontend/src/lib/oauthReturn.ts
  - frontend/src/components/AuthGate.tsx [expanded above]
```

For a landing design call, prefer `LandingScreen.tsx`, `PrismMark.tsx`, `tokens.ts`, and the compact token section of `theme.md`. `AuthScreen.tsx` remains useful only to understand the destination at `/#/`; it should not dictate the public landing composition.

## 2. `/#/` — Authenticated home

Entry: `frontend/src/components/HomeScreen.tsx`

Composed by: `frontend/src/App.tsx` home branch with the authenticated app header closure.

Summary: studio home/dashboard with high-level actions and live job counts.

Dependencies:

```text
- frontend/src/components/HomeScreen.tsx
  - frontend/src/lib/api.ts [API closure]
  - frontend/src/lib/role.ts [Role closure]
  - frontend/src/lib/useCountUp.ts
    - frontend/src/components/motion.ts
  - frontend/src/components/tokens.ts
- frontend/src/components/AppHeader.tsx [Authenticated app header closure]
```

## 3. `/#/pad` and `/#/pad/<intake-id>` — PAD quotation form

Entry: `frontend/src/components/BriefForm.tsx`

Composed by: `frontend/src/App.tsx` pad branch, alongside `QuotationHeader` and `ErrorNotice`, inside the authenticated app header closure.

Summary: fixed-height, step-based quotation input; an optional intake ID preloads client scope and attachments.

Dependencies:

```text
- frontend/src/components/QuotationHeader.tsx
  - frontend/src/components/tokens.ts
- frontend/src/components/BriefForm.tsx
  - frontend/src/components/FieldRow.tsx
    - frontend/src/components/InfoHint.tsx
  - frontend/src/components/InfoHint.tsx [already listed]
  - frontend/src/components/ImageDropzone.tsx
  - frontend/src/components/CurrencySelect.tsx
    - frontend/src/lib/api.ts [API closure]
    - frontend/src/lib/currencies.ts
      - frontend/src/types.ts
    - frontend/src/components/Dropdown.tsx
    - frontend/src/types.ts
  - frontend/src/components/Dropdown.tsx [already listed]
  - frontend/src/components/KindPicker.tsx
    - frontend/src/components/tokens.ts
    - frontend/src/types.ts
  - frontend/src/components/StepRail.tsx
  - frontend/src/components/SubmitTicker.tsx
    - frontend/src/components/tokens.ts
  - frontend/src/components/JobStrip.tsx
    - frontend/src/lib/format.ts
    - frontend/src/components/ProgressBar.tsx
    - frontend/src/types.ts
  - frontend/src/components/tokens.ts
  - frontend/src/lib/format.ts
  - frontend/src/lib/api.ts [API closure]
  - frontend/src/lib/openAttachment.ts
    - frontend/src/lib/api.ts [API closure]
    - frontend/src/types.ts
  - frontend/src/types.ts
- frontend/src/components/ErrorNotice.tsx
- frontend/src/components/AppHeader.tsx [Authenticated app header closure]
```

## 4. `/#/quotations` — PAD quotations

Entry: `frontend/src/components/QuotationList.tsx`

Composed by: `frontend/src/App.tsx` quotations branch with the authenticated app header closure.

Summary: searchable/filterable quotation list with row actions and role-dependent destructive actions.

Dependencies:

```text
- frontend/src/components/QuotationList.tsx
  - frontend/src/lib/api.ts [API closure]
  - frontend/src/lib/format.ts
  - frontend/src/components/RowMenu.tsx
  - frontend/src/lib/role.ts [Role closure]
  - frontend/src/components/tokens.ts
  - frontend/src/types.ts
- frontend/src/components/AppHeader.tsx [Authenticated app header closure]
```

## 5. `/#/q/<quotation-id>` — Quotation reader

Entry: route composition in `frontend/src/App.tsx`; principal content entry is `frontend/src/components/ResultSheets.tsx`.

Layout: authenticated header plus naturally scrolling `max-w-sheet` document layout, not the pinned app shell.

Summary: restores a quotation by ID, presents tiers and generated sheets, and exposes a revision panel.

Dependencies:

```text
- frontend/src/components/QuotationHeader.tsx
  - frontend/src/components/tokens.ts
- frontend/src/components/ErrorNotice.tsx
- frontend/src/components/TierSwitcher.tsx
  - frontend/src/lib/format.ts
  - frontend/src/components/tokens.ts
  - frontend/src/types.ts
- frontend/src/components/QuotationNotice.tsx
  - frontend/src/lib/format.ts
  - frontend/src/types.ts
- frontend/src/components/ResultSheets.tsx
  - frontend/src/lib/api.ts [API closure]
  - frontend/src/lib/format.ts
  - frontend/src/components/SheetHeader.tsx
    - frontend/src/lib/api.ts [API closure]
    - frontend/src/components/tokens.ts
  - frontend/src/components/MarkdownView.tsx
  - frontend/src/components/LineItemTable.tsx
    - frontend/src/lib/format.ts
    - frontend/src/components/StampTotal.tsx
      - frontend/src/lib/format.ts
    - frontend/src/components/tokens.ts
    - frontend/src/types.ts
  - frontend/src/types.ts
- frontend/src/components/RevisePanel.tsx
  - frontend/src/lib/format.ts
  - frontend/src/components/SubmitTicker.tsx
    - frontend/src/components/tokens.ts
  - frontend/src/components/JobStrip.tsx
    - frontend/src/lib/format.ts
    - frontend/src/components/ProgressBar.tsx
    - frontend/src/types.ts
  - frontend/src/components/tokens.ts
  - frontend/src/lib/api.ts [API closure]
  - frontend/src/types.ts
- frontend/src/components/AppHeader.tsx [Authenticated app header closure]
```

## 6. `/#/proposals` — Proposal builder

Entry: `frontend/src/components/ProposalStudio.tsx`

Composed by: `frontend/src/App.tsx` proposals branch with the authenticated app header closure.

Summary: selects an eligible quotation, builds a client proposal asynchronously, and shows build progress.

Dependencies:

```text
- frontend/src/components/ProposalStudio.tsx
  - frontend/src/lib/api.ts [API closure]
  - frontend/src/lib/format.ts
  - frontend/src/lib/useJobWatch.ts [Job-watch closure]
  - frontend/src/components/JobStrip.tsx
    - frontend/src/lib/format.ts
    - frontend/src/components/ProgressBar.tsx
    - frontend/src/types.ts
  - frontend/src/components/tokens.ts
  - frontend/src/types.ts
- frontend/src/components/AppHeader.tsx [Authenticated app header closure]
```

## 7. `/#/documents` — Generated proposals

Entry: `frontend/src/components/ProposalList.tsx`

Composed by: `frontend/src/App.tsx` documents branch with the authenticated app header closure.

Summary: lists generated proposal documents with totals, dates, open/download actions, and admin deletion.

Dependencies:

```text
- frontend/src/components/ProposalList.tsx
  - frontend/src/lib/api.ts [API closure]
  - frontend/src/lib/format.ts
  - frontend/src/components/RowMenu.tsx
  - frontend/src/lib/role.ts [Role closure]
  - frontend/src/components/tokens.ts
  - frontend/src/types.ts
- frontend/src/components/AppHeader.tsx [Authenticated app header closure]
```

## 8. `/#/intakes` — Client requests

Entry: `frontend/src/components/IntakeListScreen.tsx`

Composed by: `frontend/src/App.tsx` intakes branch with the authenticated app header closure.

Summary: request list with status/revision history, attachment access, client-link creation, and conversion into a prefilled PAD.

Dependencies:

```text
- frontend/src/components/IntakeListScreen.tsx
  - frontend/src/lib/api.ts [API closure]
  - frontend/src/lib/format.ts
  - frontend/src/components/RowMenu.tsx
  - frontend/src/components/SendToClientDialog.tsx
    - frontend/src/lib/api.ts [API closure]
    - frontend/src/types.ts
  - frontend/src/lib/role.ts [Role closure]
  - frontend/src/components/tokens.ts
  - frontend/src/lib/openAttachment.ts
    - frontend/src/lib/api.ts [API closure]
    - frontend/src/types.ts
  - frontend/src/types.ts
- frontend/src/components/AppHeader.tsx [Authenticated app header closure]
```

## 9. `/#/settings` — Studio settings

Entry: `frontend/src/components/SettingsPanel.tsx`

Composed by: `frontend/src/App.tsx` settings branch with the authenticated app header closure. Non-admin users receive an access notice instead of this component.

Summary: studio defaults, rates, policy clauses, templates, document design, and workspace deletion.

Dependencies:

```text
- frontend/src/components/SettingsPanel.tsx
  - frontend/src/lib/api.ts [API closure]
  - frontend/src/lib/format.ts
  - frontend/src/types.ts
  - frontend/src/components/CurrencySelect.tsx
    - frontend/src/lib/api.ts [API closure]
    - frontend/src/lib/currencies.ts
      - frontend/src/types.ts
    - frontend/src/components/Dropdown.tsx
    - frontend/src/types.ts
  - frontend/src/components/DangerZone.tsx
    - frontend/src/lib/api.ts [API closure]
    - frontend/src/types.ts
    - frontend/src/components/tokens.ts
  - frontend/src/components/DesignEditor.tsx
    - frontend/src/components/Dropdown.tsx
    - frontend/src/components/InfoHint.tsx
    - frontend/src/components/tokens.ts
    - frontend/src/types.ts
  - frontend/src/components/Dropdown.tsx [already listed]
  - frontend/src/components/InfoHint.tsx [already listed]
  - frontend/src/components/PolicyEditor.tsx
    - frontend/src/types.ts
    - frontend/src/components/tokens.ts
  - frontend/src/components/TemplateEditor.tsx
    - frontend/src/types.ts
    - frontend/src/components/tokens.ts
  - frontend/src/components/tokens.ts
- frontend/src/components/AppHeader.tsx [Authenticated app header closure]
```

## 10. `/#/c/<client-token>` — Client portal

Entry: `frontend/src/components/client/ClientShell.tsx`

Composed by: the client-token branch in `frontend/src/main.tsx`; it deliberately bypasses `AuthGate`, `App`, and the authenticated app header closure.

Summary: state-driven public intake experience: open form, waiting status, quotation acceptance/revision, or closed-link notice.

Dependencies:

```text
- frontend/src/components/client/ClientShell.tsx
  - frontend/src/lib/clientApi.ts
    - frontend/src/types.ts
  - frontend/src/components/tokens.ts
  - frontend/src/components/client/ClientClosed.tsx
    - frontend/src/components/tokens.ts
  - frontend/src/components/client/ClientForm.tsx
    - frontend/src/components/FieldRow.tsx
      - frontend/src/components/InfoHint.tsx
    - frontend/src/components/ErrorNotice.tsx
    - frontend/src/components/KindPicker.tsx
      - frontend/src/components/tokens.ts
      - frontend/src/types.ts
    - frontend/src/components/client/ClientDropzone.tsx
      - frontend/src/lib/format.ts
      - frontend/src/types.ts
      - frontend/src/lib/clientApi.ts
        - frontend/src/types.ts
    - frontend/src/components/tokens.ts
    - frontend/src/lib/clientApi.ts
      - frontend/src/types.ts
    - frontend/src/types.ts
  - frontend/src/components/client/ClientQuotation.tsx
    - frontend/src/components/MarkdownView.tsx
    - frontend/src/components/StampTotal.tsx
      - frontend/src/lib/format.ts
    - frontend/src/components/ErrorNotice.tsx
    - frontend/src/components/tokens.ts
    - frontend/src/lib/format.ts
    - frontend/src/lib/clientApi.ts
      - frontend/src/types.ts
    - frontend/src/types.ts
  - frontend/src/components/client/ClientWaiting.tsx
    - frontend/src/lib/format.ts
    - frontend/src/components/tokens.ts
    - frontend/src/types.ts
  - frontend/src/types.ts
```

## Other mapped screens

The complete URL mapping is in `routes.md`. Lower-priority page entries not expanded here because of the ten-page limit are:

- `frontend/src/components/JobList.tsx` — `/#/jobs`
- `frontend/src/components/WorkspacesScreen.tsx` — `/#/workspaces` and the zero-workspace override
- `frontend/src/components/TeamScreen.tsx` — `/#/teams`
- `frontend/src/components/IntakeScreen.tsx` — `/#/intakes/new`
- `frontend/src/components/ProfileScreen.tsx` — `/#/profile`
- `frontend/src/components/InviteScreen.tsx` — `/#/invite/<token>`
- `frontend/src/components/ProposalView.tsx` — `/#/p/<document-id>`
