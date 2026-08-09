# Extractable component catalog

This is a bounded menu of components with durable visual patterns. Source is included in full in `components.md` or `layouts.md`; page-specific quotation editors, tables, and one-off screens are intentionally excluded.

## Layout components

### AppHeader

- Source: `frontend/src/components/AppHeader.tsx`
- Category: layout
- Description: Shared authenticated top bar with workspace context, global navigation, notifications, theme, account menu, and optional close action.
- Extractable props: `screenName`, `studioName`, `onClose`, `theme`, `onToggleTheme`, `width`
- Hardcoded: PRISM wordmark, account-menu destinations, icon paths, child-control order, Tailwind classes

### AuthGate

- Source: `frontend/src/components/AuthGate.tsx`
- Category: layout
- Description: Root boundary that selects loading, configuration-error, signed-out landing, or authenticated app content.
- Extractable props: none; gate/session state is internal (`children` is the authenticated content slot)
- Hardcoded: configuration-error copy, LandingScreen selection, loading canvas, Tailwind classes

### LandingScreen

- Source: `frontend/src/components/LandingScreen.tsx`
- Category: layout
- Description: Responsive split landing composition with a product narrative and a fixed-width authentication card.
- Extractable props: none currently
- Hardcoded: PRISM label, hero copy, proof rows, PrismMark/AuthScreen placement, breakpoints and all classes

### AuthScreen

- Source: `frontend/src/components/AuthScreen.tsx`
- Category: layout
- Description: Stateful single-card authentication flow covering sign-in, email code, registration, reset, and OAuth.
- Extractable props: none; `view`, `busy`, credential values, and toast state are internal
- Hardcoded: auth copy, Google/Facebook provider marks, field order, icon paths, card geometry and classes

### WorkspaceMenu

- Source: `frontend/src/components/WorkspaceMenu.tsx`
- Category: layout
- Description: Header workspace switcher and anchored workspace menu.
- Extractable props: none; active workspace and menu rows are internal
- Hardcoded: menu heading, manage destination, chevron/dot artwork, row layout and classes

## Basic components

### PrismMark

- Source: `frontend/src/components/PrismMark.tsx`
- Category: basic
- Description: Theme-aware PRISM logo tile and spectrum.
- Extractable props: none for state/navigation (`size`, `title`, and `className` remain its rendering API)
- Hardcoded: prism geometry, tile radius, spectrum colors, SVG view box

### CommandBar

- Source: `frontend/src/components/CommandBar.tsx`
- Category: basic
- Description: Global quick-navigation trigger and searchable command dialog.
- Extractable props: none; open/query/cursor state and destinations are internal
- Hardcoded: destination catalog, keyboard shortcuts, search/result labels, icons and classes

### NotificationBell

- Source: `frontend/src/components/NotificationBell.tsx`
- Category: basic
- Description: Notification button with unread state and anchored event list.
- Extractable props: none; open state, unread count, notes, and read actions are internal
- Hardcoded: alert-kind mapping, bell/dot artwork, empty copy, jobs destination and classes

### Dropdown

- Source: `frontend/src/components/Dropdown.tsx`
- Category: basic
- Description: Accessible listbox field with selected, placeholder, disabled, hint, and focus states.
- Extractable props: `value`, `onChange`, `disabled`
- Hardcoded: chevron artwork, popup anchoring, option row structure and default classes

### FieldLabel

- Source: `frontend/src/components/FieldRow.tsx`
- Category: basic
- Description: Consistent uppercase field label with optional adjacent InfoHint.
- Extractable props: none for state/navigation
- Hardcoded: label typography, spacing, hint placement and fallback accessible label

### InfoHint

- Source: `frontend/src/components/InfoHint.tsx`
- Category: basic
- Description: Accessible information trigger and anchored explanatory popover.
- Extractable props: none; open state is managed by Headless UI
- Hardcoded: information icon geometry, panel width/anchor, typography and classes

### ErrorNotice

- Source: `frontend/src/components/ErrorNotice.tsx`
- Category: basic
- Description: Inline recovery-oriented alert with optional HTTP code and dismiss control.
- Extractable props: `code`, `onDismiss`
- Hardcoded: alert dot, close icon, message hierarchy and classes

### ProgressBar

- Source: `frontend/src/components/ProgressBar.tsx`
- Category: basic
- Description: Linear progress indicator with known/unknown, tone, and active-work states.
- Extractable props: `value`, `tone`, `live`
- Hardcoded: 8px track, accent/alert colors, 450ms transition and classes

### ProgressRing

- Source: `frontend/src/components/ProgressBar.tsx`
- Category: basic
- Description: Compact circular rendering of the same server-reported progress value.
- Extractable props: `value`
- Hardcoded: ring math, 5px strokes, accent/hairline tokens and transition

### TabStrip

- Source: `frontend/src/components/TabStrip.tsx`
- Category: basic
- Description: Segmented tab selector with roving focus and arrow-key navigation.
- Extractable props: `current`, `onSelect`
- Hardcoded: tab semantics, keyboard mapping, segmented-control styling and id pattern

### Toaster

- Source: `frontend/src/components/Toaster.tsx`
- Category: basic
- Description: Fixed responsive toast rail for alert and status messages.
- Extractable props: `toasts`, `onDismiss`
- Hardcoded: bottom/right placement, alert/note treatment, close icon and responsive widths

### AuthCard

- Source: `frontend/src/components/AuthScreen.tsx` (`Card` internal component)
- Category: basic
- Description: Repeated card frame used by every AuthScreen view.
- Extractable props: none for state/navigation
- Hardcoded: PrismMark, 24rem width, rounded paper surface, padding, shadow and footer placement
