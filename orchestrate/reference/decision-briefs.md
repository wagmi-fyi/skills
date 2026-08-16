# Decision briefs — illustrating a crossroads visually

When a design crossroads is **complex, meaty, or visual/UI**, don't escalate it as a wall of text — **render it.** A good brief lets the human rule in seconds instead of minutes. This is the high-fidelity tier of the crossroads contract (`human-in-the-loop.md`).

## When (scale fidelity to the decision's weight)
- **Trivial** (binary, obvious-once-stated) → chat text, or a quick `AskUserQuestion`.
- **Complex / meaty / UI / visual / multi-option-with-real-trade-offs** → a **decision brief**: an HTML file written to `/tmp/<slug>.html`, opened with `scripts/present`, paired with `scripts/notify` + pause-the-unit + a bus-inbox pointer.

## Two modes
1. **Interactive mockup** (UI / visual choices) — render each option as **real UI** in one file with a control bar to **toggle between options**, and a short note framing the trade-off. Let the human see the actual thing, not a description of it.
2. **Dossier comparison** (meaty non-UI choices — architecture, schema, sequencing) — the options side by side with their trade-offs, a clear **recommendation**, and a short Q&A if it helps.

## On the look — your judgment, not a house style
**Match the project's own design language / vibe** when it has one: glance at its UI, its existing mockups, its palette and type, and make the brief feel native to what you're building. If there's **no** established design language, pick something **modern, clean, and easy to read.** Don't impose a fixed aesthetic and don't reach for a canned template — the right look is a judgment call, and it's yours. Keep it legible and fast to scan; that matters more than any particular style.

## Every brief includes (structure, not style)
- The **decision** in one line + **why it's a crossroads** (what the plan/intent doesn't settle).
- The **options**, each clearly shown (rendered, for UI), with their **trade-offs / costs**.
- Your **recommendation** — you frame; you don't abstain.
- A clear **how-to-choose** affordance ("reply A / B, or tell me to adjust").

## Capture — available, not default, never forbidden
Default is **illustrate-only**: the human rules in chat or the paused pane. No hard "never capture" rule — if a decision is high-value enough that closing the loop in-browser clearly pays, an agent may serve the brief from a tiny local http listener whose buttons write the choice back to the bus and auto-resume the unit. Weigh cost/benefit; reach for it only when it earns its keep.

## Flow
1. Write the brief to `/tmp/<slug>.html` — in a look that fits the project (or modern/clean if none).
2. `scripts/present /tmp/<slug>.html` — opens it in the browser.
3. `scripts/notify "<decision>" "<one line + your rec>"` **+ pause the unit**.
4. `bus send <you> <human-handle> "crossroads: <decision>" "see the brief" --ref /tmp/<slug>.html`.
5. Resume on the ruling; record the decision + rationale in the workpaper journal.
