# Manual Accessibility Test Checklist

This checklist defines manual verification protocols for Makan Together across desktop and mobile screen readers, high-zoom environments, keyboard navigation, and cognitive accessibility.

## 1. Keyboard Navigation and Focus Management

- [ ] **Tab order follows visual layout**: Tab order proceeds sequentially through navigation, header actions, main content cards, and forms without unexpected jumps.
- [ ] **Visible focus indicator**: All interactive controls (buttons, links, form inputs, checkboxes, radios) display high-contrast visible focus rings.
- [ ] **Modal focus trapping & return**:
  - Opening any dialog (Start Meal, Edit Plan, Voting, Report Issue, Delete Account) places initial focus inside the dialog.
  - Tabbing stays trapped within the open modal.
  - Pressing `Escape` closes the modal.
  - Closing a modal returns keyboard focus to the exact control that triggered it.
- [ ] **No keyboard traps**: Every screen and component can be navigated in and out of using only `Tab`, `Shift+Tab`, `Enter`, `Space`, and arrow keys.

## 2. Screen Reader Verification (VoiceOver / TalkBack / NVDA)

- [ ] **Headings hierarchy**: Page structure uses logical heading levels (`h1`, `h2`, `h3`) allowing screen-reader heading navigation.
- [ ] **Form control labels**:
  - Every `<input>`, `<select>`, and `<textarea>` has an associated `<label for="...">`.
  - Radio button groups use `<fieldset>` and `<legend>`.
- [ ] **Status and error announcements**:
  - Asynchronous form errors receive immediate screen reader focus (`role="alert"`).
  - Transient toast messages and page transitions are announced politely via `aria-live="polite"` (`#toast`, `#page-announcement`).
- [ ] **Icons and graphics**: Decorative SVG icons use `aria-hidden="true"`; interactive icons have descriptive accessible names.
- [ ] **Bilingual pronunciation**: `html[lang]` attribute accurately updates to `en` or `ms` when interface language is toggled.

## 3. Zoom, Reflow, and Display Adaptations

- [ ] **320 px narrow viewport**: Entire application reflows vertically without horizontal scrollbars at 320 px width.
- [ ] **200% text zoom**: Content remains readable and fully functional at 200% browser text scaling without clipping, truncation, or overlapping text.
- [ ] **Prefers-reduced-motion**: Animations and transitions are completely disabled when `prefers-reduced-motion: reduce` is active.
- [ ] **Contrast**: Normal text achieves at least 4.5:1 contrast against its background; large text and UI components achieve at least 3.0:1.
