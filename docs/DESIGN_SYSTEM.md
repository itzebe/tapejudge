# TapeJudge — design system

The single reference for TapeJudge's interface. It is specific to this product:
a competition ledger that makes every decision traceable. Read it before
changing any template or stylesheet.

## 1. The idea

TapeJudge is not a dashboard and not a SaaS marketing page. It is a **competition
record** — the document a competition produces when its rule is fixed and its
arithmetic is reproducible. The interface should read like a well-typeset
publication that is also a serious instrument: the front page argues, the
application records, the verification page audits.

**Remembered for:** a warm, editorial ledger where the frozen rule and the
reproducible ranking look like formal records rather than dashboard widgets.

The three weaknesses this system fixes:

1. The landing page was a bare table. It never said what TapeJudge is, why a
   rule that cannot change matters, or what to do next.
2. The states that matter most — *frozen*, *prepared*, *published*,
   *verified* — were plain text rows with no visual weight, so the honesty model
   was invisible.
3. The visual language was a neutral grey template; it carried no point of view
   and gave organisers no reason to trust the instrument.

## 2. Art direction

A **warm paper ledger crossed with a printed register.** Deep ink sections
against warm ivory; a single rust accent used for meaning, never decoration;
editorial serif for statements, a humanist sans for controls, and monospace for
every piece of evidence (digests, hashes, circuit ids, call data).

The mood: credible, calm, exact. The opposite of a crypto dashboard.

## 3. Colour

Defined as CSS custom properties in `tapejudge/web/static/style.css`.

| Token | Value | Role |
|---|---|---|
| `--paper` | `#f4efe4` | Page ground — warm ivory |
| `--paper-2` | `#ece5d6` | Recessed ground |
| `--surface` | `#fbf8f1` | Cards, panels |
| `--ink` | `#1b1712` | Primary text, warm near-black |
| `--ink-2` | `#4b443a` | Secondary text |
| `--ink-3` | `#7c7365` | Muted / meta |
| `--line` | `#ddd2bd` | Hairline borders |
| `--line-2` | `#c7b99f` | Stronger borders, inputs |
| `--dark` | `#17130e` | Deep ink sections |
| `--dark-2` | `#211b14` | Raised dark surfaces |
| `--on-dark` | `#f2ecdf` | Text on dark |
| `--on-dark-2` | `#b3a893` | Muted text on dark |
| `--accent` | `#a5431d` | Rust — primary action, "frozen", rank 1 |
| `--accent-2` | `#8a3717` | Accent hover |
| `--accent-soft` | `#f6e6da` | Accent tint |
| `--accent-line` | `#dfb79c` | Accent border |
| `--ok` | `#2f6b45` | Verified / settled |
| `--ok-soft` | `#e5efe7` | Verified tint |
| `--warn` | `#8a5a00` | Honest caution |
| `--warn-soft` | `#f7edd4` | Caution tint |
| `--err` | `#9b2222` | Failure / withheld |
| `--err-soft` | `#f7e2df` | Failure tint |

The accent means exactly one thing: **this is the fixed thing / the primary
action / the winning rank.** It is never used for decoration. Semantic colours
(ok / warn / err) are reserved for state.

Contrast: every text/background pair is at least 4.5:1 (normal) and 3:1 (large);
non-text borders meet 3:1. The palette is deliberately warm but not low-contrast.

## 4. Typography

Three faces, three jobs.

- **Display — Fraunces** (self-hosted variable serif, `font-optical-sizing: auto`).
  Used for `h1`, the landing headline, the wordmark, and emphasised statements.
  Its optical size axis gives large text a high-contrast editorial cut.
- **Text — system sans** (`system-ui, -apple-system, "Segoe UI", Roboto, …`).
  Body copy, labels, controls. Never used for large display text.
- **Mono — system mono.** Every digest, hash, circuit id, transaction hash,
  timestamp, gate count and call fragment. Evidence is monospaced.

Type scale (fluid where it matters):

| Role | Size |
|---|---|
| Display / hero | `clamp(2.4rem, 5.5vw, 3.9rem)` |
| H1 | `clamp(1.6rem, 3vw, 2.05rem)` |
| H2 | `1.3rem` |
| H3 | `1.05rem` |
| Body | `1rem` / line-height `1.6` |
| Small | `0.85rem` |
| Eyebrow | `0.72rem`, uppercase, `letter-spacing .16em` |

Measure is capped at `66ch` for prose. Headlines are set tight
(`letter-spacing -0.01em`, `line-height 1.08`).

Fonts are vendored under `tapejudge/web/static/fonts/` (latin + latin-ext only,
~280 KB). No runtime third-party font origin, so the CSP is unchanged.

## 5. Space, grid and surfaces

- Spacing rhythm: `--s1:4 --s2:8 --s3:12 --s4:16 --s5:24 --s6:32 --s7:48 --s8:72`.
  Whitespace groups and separates; it is not equal padding on every box.
- Layout width: `--wrap: 1080px`; prose width `--measure: 66ch`.
- Radius is small and consistent: `--radius: 4px`, `--radius-lg: 6px`.
- Borders are hairlines (`1px var(--line)`); cards carry a very subtle shadow
  (`0 1px 0` + `0 10px 24px -18px`), never a glow.
- Grid: a two-column split (`.split`) collapses to one column at 720px; a
  three-column band (`.cols-3`) collapses at 900px.

## 6. Components

- **`.eyebrow`** — a stamped uppercase category label above a heading.
- **`.hero`** — editorial opening: eyebrow, display headline, lede, actions, and
  a composed **ledger preview** (a rule card and a result card, overlapping).
- **`.ledger-preview`** — a small, real composition of product surfaces, not a
  fake analytics chart.
- **`.flow`** — the four-step lifecycle (freeze → evaluate → rank → verify) as
  numbered records.
- **`.dark-band`** — a deep-ink section (used once, for the "why this matters"
  contrast), with `.on-dark` text and an inverted button.
- **`.state-rail`** — the publication progression as ordered chips:
  *Local circuit → Prepared → Awaiting signature → Broadcast → Confirmed →
  Independently verified*. The current state is marked; unreached states are
  dimmed. This is the honest model made visible.
- **`.badge`** — a status chip (`draft`, `frozen`, `open`, `judging`,
  `settled`, `private`), tinted per semantic role.
- **`.panel`** — a bordered card on `--surface`.
- **`.notice`** — a left-ruled callout (`ok` / `warn` / `err` / `info`).
- **`.meter`** — a criterion weight bar (analytical, not decorative).
- **`.rank-table`** — the ranking as a formal result; rank 1 carries the accent.
- **`.codeblock`** — dark mono block for evidence.
- **`.kv`** — a definition list for identifiers and metadata.

## 7. Motion

Restrained and purposeful only: a short fade-and-rise on the hero and section
reveals (`IntersectionObserver`, progressive enhancement), button and link
transitions, and status chip transitions. Everything is disabled under
`prefers-reduced-motion: reduce`. No motion decorates empty space.

## 8. Accessibility (WCAG 2.2 AA)

- One `h1` per page; headings in order.
- Visible focus ring on every interactive element (`:focus-visible`).
- Every control has an accessible name; every image has `alt`.
- Tap targets ≥ 44px on mobile for primary controls, ≥ 24px minimum everywhere.
- No horizontal overflow at 320–1440px; wide tables scroll inside their panel.
- Errors are announced (`role="alert"`) and identified in plain language; a
  field-level error is marked `aria-invalid`.
- Verified by `scripts/a11y_audit.mjs` at 390 / 768 / 1440 px.

## 9. Honesty rules encoded in the UI

These are product rules, not styling choices; the design must never contradict
them.

- A **locally frozen** circuit is never presented as on-chain. The verify page
  says "Not published on chain" unless `publication.on_chain` is true.
- The **state rail** shows only states the record can prove.
- The **ranking** is presented as a computed outcome with its inputs shown, not
  as an opaque score.
- Synthetic data is labelled `SYNTHETIC` wherever it appears.
- Copy says **Pay/record/verify**, never implies the app signs or sends a
  transaction.
