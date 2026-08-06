# Design Plan — "Premium Dark" refinement for HR Academy

Scope: templates/_style.html, login.html, dashboard.html, entries.html, row.html only. No routes, no Python, no changes to ids/names/hx-* attributes or the JS hooks (.row, .name, .roll, .tick, .retry, data-status, is-*, .failed, hidden handling). All tap targets stay ≥ 44px. All motion stays gated behind the existing prefers-reduced-motion block.

## 1. Global design system (_style.html)
The current design is a good foundation but flat: one card color, one shadow, saturated state fills, no ambient light. These changes build the premium layer everything else sits on.
1.1 Ambient lighting. Add a faint warm radial glow at the top of the page (radial-gradient(120% 45% at 50% -5%, rgba(255,232,200,.045), transparent 60%), painted as a fixed background layer on body so it doesn't scroll). This is the single biggest "premium" cue in dark UI — surfaces read as lit from above rather than cut from black paper.
1.2 Elevation system — three graduated levels instead of one:

- Level 0 (page): --paper + ambient glow.

- Level 1 (cards, datebar, tiles): keep --card, add a layered shadow: --lift becomes 0 1px 2px rgba(0,0,0,.45), 0 6px 20px rgba(0,0,0,.22) — soft, diffuse, still subtle. Keep the hairline top-edge highlight (--edge).

- Level 2 (raised controls: sign-in button, checked profile, active toggle half): slightly brighter surface + a soft colored ambient glow matching its state hue (e.g. 0 2px 14px rgba(55,133,78,.28) on the active "present"). Colored glow at low alpha is what makes state changes feel "lit" rather than recolored.

1.3 New tokens: --glow-green, --glow-rose, --glow-amber, --glow-brand (scarlet at ~10% alpha), and a motion trio: --ease: cubic-bezier(.22,.61,.36,1), --dur: .16s, --dur-enter: .28s. Existing hues (--green, --terracotta, --amber, tints) are unchanged — semantics stay.

1.4 Typography fix. Archivo is currently served at only 500 and 700, so every font-weight: 550/560/640 in the app silently renders as 500. Request wght@500;600;700 and move those declarations to real 600. Headings keep 700 with tightened tracking. Zero visual regression risk, crisper hierarchy.

1.5 Page-width consistency. entries.html currently has no max-width (login is 23rem, dashboard 30rem). Give the entries body max-width: 34rem; margin: 0 auto so all three pages share one centered column on anything wider than a phone.

1.6 Shared micro-motion language (all pages):
- Press: scale(.97–.985) at 100ms (already present — kept).
- Entrances: fade + 8px rise, .28s var(--ease), staggered ≤ 60ms, capped so long lists don't wave.
- Glow/color transitions: 140–180ms.
- One subtle 200ms page fade-in on load for polish between navigations.

## 2. Login page
- 2.1 Brand glow: a soft scarlet radial (--glow-brand, blurred, ~40% width) behind the logo, plus a gentle drop-shadow under the PNG. The "HR" reads as floating above a lit stage — this is where the brand red earns its keep.
- 2.2 Form rise: the form currently only fades; give it the same 8px rise as the logo so the whole screen lands as one choreographed moment (logo 600ms, form overlapping at 300ms, both kept subtle).
- 2.3 Profile cards: checked state gains the green ring + soft green glow (Level 2 treatment) instead of just a flat fill swap; radius unified to --radius (12px) to match the rest of the system.
- 2.4 Sign-in button: keep the solid light button (it reads premium on dark, like Apple's) but add a hairline top inner-highlight and the deeper Level-2 shadow so it reads as physical.
- 2.5 Error message: small fade + rise entrance when it renders, so a failed sign-in feels handled rather than abrupt.

## 3. Dashboard
- 3.1 Icon chips: the bare Tabler glyphs become rounded-square chips (styled directly on the existing <i> elements — no markup change): green-tinted chip with green icon on the live tile, neutral sunken chip on the two "soon" tiles. This one change makes the tiles feel like a designed product.
- 3.2 Tile surfaces: gentle vertical gradient (top ~2% lighter) + the new layered shadow; the green left edge stays as the signature. Chevron nudges 2px right on :active alongside the existing press scale.
- 3.3 Staggered entrance: header fades, then three tiles rise in 60ms apart. Total added time ~400ms, subtle.
- 3.4 Header divider: the hairline becomes a gradient line that fades at both edges — a quiet premium detail.

## 4. Daily entries page (the workhorse)
- 4.1 Sticky frosted date bar — the headline improvement. Restructure the bar so line 1 (prev · date · badge · next) becomes position: sticky with backdrop-filter: blur(14px) and a translucent state-tinted background (green tint today, amber for past dates, solid-tint fallback under @supports). Scrolling 40 students currently loses the one fact the screen is about; after this, "which day am I writing to?" is always visible, frosted-glass is the hallmark premium mobile pattern, and your overflow-x: clip choice already permits sticky. The bar also gains a faint state-colored ambient glow at its bottom edge. The native date picker moves to its own full-width line below the sticky strip so the sticky zone stays ~52px tall.
- 4.2 Prev/next: keep text labels (teachers aren't power users — icons alone are a risk) but restyle as compact ghost pills, consistent with the dashboard's logout link.
- 4.3 Search: add a decorative magnifier icon inside the field (additive markup only), deeper recessed background, existing focus glow kept.
- 4.4 Row cards: keep the 12-col grid and left state edge — but rows now sit slightly tighter (~12% less vertical space: padding .8rem → .7rem, row-gap .5rem → .45rem, marks label merged onto the score line's left as a small caps prefix). When marked, the row gains a faint outer glow in the state hue (0 0 0 1px + soft 10px blur, both low alpha) so a scrolled list reads as a column of lit edges.
- 4.5 Segmented toggle: the present/absent pair keeps its joined shape; the active half gets the Level-2 treatment — fill + matching hue glow + hairline inner highlight — and a one-time 180ms "settle" pop (scale 1→.96→1) keyed off the is-present/is-absent class, so both the optimistic tap and the server swap confirm physically. Unmarked halves stay recessed.
- 4.6 Save tick: pop-in animation (scale .5→1 + fade, 160ms) whenever hidden is removed — display:none→inline restarts CSS animations, so this works with the existing reveal mechanism untouched.
- 4.7 Failed state: keep the amber ring/spill exactly as is — semantics already good, no shake animation (feels cheap).
- 4.8 Score pair — fixes your flagged screenshot. Score and max-marks become one connected fraction control using the same technique as the toggle pair (rounded outer corners, square inner corners, shared border via negative margin, slash centered on the seam — zero markup change, pure grid/border work), with the small caps "TEST SCORE" label inline to its left. The two orphaned boxes on line 5 become a single deliberate unit, and the disabled-when-absent dimming then reads as one object fading instead of two boxes half-dying.
- 4.9 Remark: replace the ✎ text glyph with the Tabler ti-pencil icon for family consistency; open state keeps its amber tint.
- 4.10 Entrance: rows fade-rise in, staggered 25ms for the first 10 only (nth-child(-n+10)), so opening the page feels alive but a 40-student list never waves.

## 5. Explicitly out of scope
Routes, handlers, queries, HTMX attributes, the optimistic-save and failure JS, and unimplemented brief features (has_test toggle, "Finish day", progress line, calendar) — all untouched. The hidden-attribute rules, --tap: 44px, and the green/amber/rose semantics are preserved as-is.
6. Verification (after implementation)
1. Run the app; screenshot all three pages at 390×844 and 320px widths — check overflow, tap targets, contrast.
2. Mark a student present/absent, blur a topic, force a failed save — confirm tick pop, settle pop, glow states, and amber failure all fire with the existing JS unmodified.
3. Scroll the entries list — confirm the sticky frosted bar and that rows slide under it.
4. Enable "Reduce Motion" in OS dev tools — confirm everything is instant.
5. Confirm the score-pair fix against the layout flagged in Issue images/SCR-20260803-sfas.png.
