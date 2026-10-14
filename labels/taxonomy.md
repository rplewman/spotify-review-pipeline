# Label taxonomy (schema-v1)

The first section is copied **verbatim** from `GRADING_CONTRACT.md` ("Common labels for fair comparison").
The second section adds our conventions for the fields the contract leaves open. Worked examples are in
[`examples.yaml`](examples.yaml) — taken from development files only (`analysis_10000` minus `dev_100`), never from the golden 50
or `dev_100` (a test enforces this).

## 1. Contract definitions (verbatim)

Use these exact primary labels in the grading export. You may add custom subtopics and richer internal labels in other columns/files.
Classify the review text itself. Keep stars and other metadata for analysis; do not use them to substitute for interpreting the text.

| `topic` | Definition |
|---|---|
| `access` | Login, signup, password or account access |
| `usability` | Navigation, controls, layout, queue/playlist management, ad interruptions |
| `playback` | Playback failure, crashes, lag, connection failures, audio quality, resource use |
| `downloads` | Downloading, saved music, offline listening, disappearing downloads |
| `catalog` | Missing songs/artists, search/discovery, recommendations, lyrics availability |
| `billing` | Price, charges, subscriptions, paywalls, premium entitlement; explicitly premium-only controls go here |
| `support` | Contacting support and the support response |
| `other` | General praise/criticism, unrelated content, or no supported specific topic |

Choose the problem with the highest supported severity. On a tie, choose the first specific problem mentioned. For a positive review, choose the first specific praised feature; general praise is `other`.
Mentioning a paid plan alone does not make the topic `billing`. A subscription failing to activate does; music crashing for a paying customer is `playback`.

Intent precedence: `cancellation` (explicitly leaving, uninstalling, cancelling, or threatening to do so) → `complaint` (negative experience, including mixed praise/criticism) → `request` (desired change without a reported failure) → `praise` → `unclear`.
Bare boycott slogans and unrelated/meaningless text are `unclear`, unless there is a product complaint or explicit personal departure. General “bad app” is a complaint; do not infer a specific defect.

| `severity` | Shared meaning |
|---|---|
| 1 | No reported problem: praise, neutral/unclear content, or a pure feature request |
| 2 | Dislike, generic criticism, minor annoyance, or a cosmetic issue; no supported functional loss |
| 3 | A degraded or restricted function; some use or workaround remains |
| 4 | A clearly blocked core task, such as inability to log in or play music |
| 5 | Explicit serious financial, privacy, or data harm; an expensive plan, a crash, or angry language alone is insufficient |

Cancellation intent does not automatically raise severity. Missing context should trigger `needs_review`; do not invent impact. Write examples of how you applied these shared definitions.

## 2. Our conventions

### Applying the rules
- **Order of decisions:** intent (by precedence) → topic (highest supported severity, then first mentioned) → severity of *that* problem.
- **Premium-only controls → `billing`.** Skips, shuffle-only, choosing a song, repeat, seeking, lyrics behind premium: the complaint is the paywall.
  Severity is usually 3 (restricted, some use remains).
- **Paid-plan mention alone ≠ `billing`.** "Even on premium it crashes" is `playback`. Billing needs a price, charge, subscription
  or entitlement problem.
- **Ads → `usability`** (ad interruptions). Ad volume alone is an annoyance: severity 2; 3 only if the text says ads stop normal use.
- **Connection failures and audio quality → `playback`.** "No internet" errors while online are playback, not access.
- **Boycott slogans → `unclear`, topic `other`, severity 1.** An explicit personal departure ("I uninstalled as a protest") is
  `cancellation`, still topic `other`, and still severity 1 — no product problem is reported.
- **General "bad app" / "worst app" → `complaint`, `other`, severity 2, `entities: []`.** Do not invent a defect.
- **Cancellation never raises severity.** Rate the underlying problem; "I'm uninstalling" over a skip limit stays 3.
- **Severity 5 needs explicit harm:** charged after cancelling, unauthorized charge, stolen account/data, privacy breach.
  "Too expensive" is billing severity 2.
- **Mixed praise + criticism → `complaint`.** "Great app but X is broken" is a complaint about X.
- **Text only.** Stars, likes, version and date are never used to decide a label (a 5-star review can be a complaint).

### Fields the contract leaves to us
- **`sentiment`** (−1..1, hand labels in steps of 0.25): −1 hostile/furious · −0.5 clearly negative · 0 neutral, unclear or balanced
  · +0.5 clearly positive · +1 enthusiastic. Mixed reviews sit between, weighted toward the dominant tone. Golden comparison uses
  MAE with a **predeclared tolerance of ±0.25** for "agreement".
- **`entities`**: product features, Spotify surfaces and named third parties **explicitly written in the text** (e.g. `shuffle`,
  `lyrics`, `smart shuffle`, `ads`, `premium`, `podcasts`, `Amazon`). Short noun phrases, as written; no inferred entities;
  `[]` is fine. Compared on the golden set as set overlap (informational).
- **`evidence_quote`**: an **exact substring** of the review text (copy, don't retype) that supports the chosen topic and severity —
  the shortest span that does. For a short review the whole text is fine.
- **`needs_review`**: `true` when the label is a guess because context is missing ("it doesn't work since the update" — what?),
  the language can't be read, or sarcasm/meaning is genuinely unclear. Otherwise `false`.
- **Accepted alternatives (golden/dev labels only):** when a second reading is genuinely defensible, record it as an accepted
  alternative for `topic`, `intent` or `severity` (the instructor's gold uses accepted lists the same way). The primary label is
  always accepted. Don't add alternatives just to hedge.
