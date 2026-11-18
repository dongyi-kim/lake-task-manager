# LTM desktop design system

This document is the design authority for Lake Task Manager. It is a standalone contract, not a generated oh-my-design System Graph package. The approved direction combines Linear's quiet work surfaces with LTM's dense operational views. Existing product behavior is authoritative; the external prototype is visual evidence, not replacement application code.

<a id="experience"></a>
## Experience

- Audience: developers, data operators and PMO staff who compare many tickets, inspect evidence and write reports.
- Keep top navigation, Task options/status axes, parent/child context, WBS tree/timeline, ticket hierarchy/body/history and Confluence navigation/body/outline in their current positions.
- Show real information and real failure states. Never replace an existing function with a disabled mock control, sample data or a success toast.
- Prioritize density in task lists and legibility in documents. A large card is not the default container for every feature.
- Desktop only: validate 1280, 1440 and 1720 px. Preserve existing narrow-window behavior, but do not add mobile-specific features or redesign mobile layouts.

<a id="foundations"></a>
## Foundations

Runtime values live in `app/static/styles/tokens.css`; keep existing token names compatible. Do not create a second palette or introduce per-view light/dark literals.

| Role | Light | Dark |
|---|---|---|
| Workspace `--bg` | #e9edf3 | #131820 |
| Content/card `--panel` | #ffffff | #2b3543 |
| Quiet surface `--panel-2` | #f4f6f9 | #202936 |
| Data lane `--panel-3` | #e1e7ef | #19222d |
| Decorative border `--border` | #c9d1dc | #445367 |
| Strong boundary `--border-hi` | #8a99ab | #657890 |
| Primary text `--text` | #202630 | #edf1f6 |
| Secondary text `--muted` | #485564 | #c0cbd8 |
| Metadata `--muted-2` | #58687b | #aebdce |
| Primary action `--accent` | #086c62 | #82d4c0 |
| Link/update/progress `--info` | #285eb0 | #a4c5ff |
| Success `--good` | #176b4c | #98d9b7 |
| Warning `--warn` | #8b5609 | #edc681 |
| Error/overdue `--danger` | #b03741 | #f4adb6 |
| Keyboard focus `--focus` | #2869c7 | #91baff |

- Separate semantic foregrounds from chart fills. Preserve deterministic categorical Epic/module colors and their current meaning.
- Selected surface is teal; unread updates are blue; deadline warnings are amber/red. Do not reuse selection as unread acknowledgement.
- Surfaces must remain distinguishable without shadows. Reserve larger shadows for overlays; cards may use a one-pixel low elevation shadow.
- Spacing scale: 4/8/12/16/24/32 px. Controls 30 px, compact controls 26 px, primary controls 32 px. Radius: controls 5 px, cards 6 px, panels 8 px, dialogs 10 px.

<a id="typography-assets"></a>
## Typography and assets

- Keep the installed/local Korean-capable system font stack; no external font or asset requests.
- Page title 22/30 px, section title 15/22 px, task title 13/20 px, document body 14/23 px, ordinary metadata 12/18 px. Compact auxiliary metadata may use 11 px where already needed for data density.
- Use tabular numerals for dates, counts and progress. Monospace is for ticket keys, code and JSON only.
- Korean titles must not break arbitrarily. Dense rows may ellipsize with the full title available through keyboard-accessible detail and accessible names. At 200% zoom allow height growth instead of clipping controls.
- Reuse existing icons. Preserve type, priority, owner/avatar, Epic/parent, VoC classification and unknown-value labels.

<a id="components-states"></a>
## Components and states

- Extend existing `TaskCard`, `DueText`, `TicketUpdateMark`, `StatusPill`, `TypeBadge`, `FieldEdit` and `CommentEditor` contracts. Do not build a universal card/editor that owns domain logic.
- Standard Task cards use two lines and target <=68 px at normal desktop scale. Inline Sub-Task rows keep their existing denser layout. Do not force every child into a two-line card.
- A task's parent/Epic context may be shown once in the group header only when the existing `showEpic` contract permits it. Preserve priority/type and the original mine/related semantics.
- Unread indication remains governed by existing update/read timestamps. Completed tickets use completion dates, not overdue styling. Preserve inherited-date markers and highest-priority urgency.
- Use consistent button/input borders, label hierarchy, focus rings and disabled treatment. Maintain each control's original command, keyboard behavior and accessibility semantics.
- Empty, loading, retained-data refresh, partial failure, permission failure and fatal failure are separate states. Share visual framing only; preserve the owner and scope of retries.
- Dialog framing may share appearance, but dirty drafts, uploads, submission receipts, approval and close guards stay with the existing component.
- Hover/focus/selected/unread/overdue may coexist. Essential meaning must have a label or icon as well as color.

<a id="layout-platforms"></a>
## Layout and platform

- Preserve all eight routes and their overlays. Full-width analytical views keep their original grid and data semantics.
- Preserve Task grouping, horizontal/vertical status axes, global paging, parent fold and all-child access. Any bounded preview must apply search/filter/sort to the whole result before slicing, and distinguish visible and matching totals.
- Documents keep the tree/body/outline layout and resizable panes. Long prose uses a readable line length without wasting space in tables or editors.
- The recent-item strip belongs below top navigation in Task and Confluence, with separate lists. It must not change existing deep links or move the ticket detail to a new drawer.
- Tab state owns IDs/order/active selection/scroll/focus only. Reuse existing scoped drafts, attachments and receipts. Restore tab metadata without fetching all inactive bodies.
- Preserve native tray/SSO/shortcuts/startup/update/download integrations. Do not rebuild native menus as web surfaces.
- Hover transitions 120 ms; overlays <=200 ms and <=4 px movement. No permanent pulsing, animated counters or automatic list reordering. Respect `prefers-reduced-motion`.

<a id="content-locales"></a>
## Content and locale

- Concise Korean labels; concrete errors name the affected item and recovery action. Preserve source workflow/type names.
- Do not expose implementation jargon in ordinary user flows. Keep engineering detail in existing diagnostic surfaces.
- Do not hide fields just to satisfy a height target. Move repeated context to its existing header and expose details on demand with keyboard support.

<a id="governance"></a>
## Governance and acceptance

- `docs/design/feature-preservation.md` owns the behavior inventory and validation mapping. Changes to appearance cannot weaken those contracts.
- Reuse the existing Vue/native ES modules stack and CSS files. Scope view-specific selectors to their view. Remove superseded duplicate declarations after checking consumers; do not add a permanent override stylesheet.
- REST/MCP APIs, formulas, query scopes, cache freshness, auth recovery, write payloads, approvals and receipts remain unchanged by styling.
- Desktop text contrast >=4.5:1; meaningful non-text indicators and focus >=3:1. Focus must remain visible and return after overlays close.
- Validate actual application DOM and actions, not just the synthetic prototype. Tests skipped for missing browser/Node dependencies do not count as passes.
- Production Jira/Confluence writes and live LLM evaluation are not part of design verification. Use mock fixtures and approved existing test infrastructure.
- All test scratch/cache/profile/output paths use an owned OS temporary directory with final cleanup. Durable design evidence is stored outside the repository, linked from the implementation record.
