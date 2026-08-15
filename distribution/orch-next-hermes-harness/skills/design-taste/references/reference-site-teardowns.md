# Reference Site Teardowns

Real, browser-measured technique catalogs from sites a user flagged as having
design sense worth acquiring: load the real page, read
`getComputedStyle`/script tags/element counts directly, don't guess from
memory or eyeball adjectives. Adjective-only entries ("すごくオシャレ") are not
allowed here — every claim needs a measured value or a named, checkable
technique, and a claim that turns out wrong on closer measurement gets
corrected in place, not left standing. Add new sites as new `##` entries.

This file catalogs *technique*, not verdicts. Which pole fits a given brief is
a Pass-1 decision grounded in that brief's own subject (see SKILL.md's Core
stance) — a maximalist ticker-collage is right for a street-culture creative
agency's recruiting page and wrong for almost everything else.

## Contents

- [recruit.zoccon.me](#recruitzocconme-株式会社on--クリエイティブプロダクション採用サイト-checked-2026-08-02) — maximalist Y2K/Harajuku collage, canvas-ticker marquees, emoji-as-typography, identity-first recruiting narrative
- [quoitworks.com](#quoitworkscom-株式会社クオートワークス--web制作会社コーポレートサイト-checked-2026-08-02) — restrained warm-neutral editorial, best SEO/LLMO implementation of the three, structural type system, a 10-section B2B trust-building sequence
- [sirup.online/5th/](#siruponline5th-sirup-5th-anniversary-special-site--音楽アーティスト記念特設-checked-2026-08-02) — WebGL color-arc drama, layered-SVG headline illustration, LocomotiveScroll gotcha
- [Cross-site synthesis](#cross-site-synthesis-whats-actually-transferable) — the 9 lessons that generalize across all three, including how to sequence information architecture by decision type
- `references/llmo-aio-evidence.md` (separate file) — AI-search citation optimization: real vendors, research, platform statements, confirmed/contested/debunked evidence table

## recruit.zoccon.me (株式会社ON — クリエイティブプロダクション採用サイト, checked 2026-08-02)

### Stack, color, structure

- Vanilla custom `main.js`, no GSAP/ScrollTrigger/Three.js detected. **52
  `<canvas>` elements** on one page — one per independently-scrolling
  marquee/ticker text line, extending down to individual phrase/word level
  (each line of the "MESSAGE" monologue is its own canvas too). This is a
  real measured performance cost, not a guess: it reproducibly hung this
  session's Electron-based browser-automation renderer on first load (fresh
  tabs, reproduced twice) and only rendered after an extended wait. If
  emulating a ticker/marquee effect, prefer a single canvas or CSS
  `background-position`/`transform` animation over one canvas per line.
- Fonts: body/headings = Noto Sans JP (free, weight 900 for H1);
  display/link text = `futura-pt-condensed` (paid condensed geometric sans,
  weight 800, the *only* non-bold text on the page is a byline caption at
  weight 400) used only for short punchy headline/link text, never body
  copy. Lesson: spend a premium display-font budget only where it's
  visually loud; free JP body fonts are not the bottleneck on "does this
  look expensive."
- Color: accent mint/teal `#00E3BB`; section bg neutral gray `#D0D0D0`;
  base bg warm-light gray `#EBEBEB` (not pure white); black ink.

### SEO

- Complete OGP/Twitter Card set and a correct self-referential canonical.
  **Zero JSON-LD structured data.** 108 `<img>` on initial load, only
  **10.2%** carry non-empty `alt` — a real accessibility/SEO weak point not
  offset by the site's visual richness.
- Heading outline is substantively real (order matches visual flow) but
  structurally messy: the H1 is image-only (SVG `alt` text, no live H1
  text); the entire hero "MESSAGE" monologue is packed into a single H2;
  several H3s are duplicated (once with natural line breaks, once
  concatenated with no spaces — evidently the source string for the
  canvas text-splitting animation); one trailing empty H3; flat 3-level
  hierarchy despite deep visual section nesting.
- `robots.txt` and `sitemap.xml` (an index pointing to 4 sub-sitemaps) both
  exist and are correctly formed — the SEO basics are in better shape than
  the messy heading structure or low alt coverage would suggest.

### 文字スタイル・空間の使い方 (typography and spacing)

- Mobile typography is **fully fluid** (`body { font-size: 3.58974vw }`
  inside the mobile breakpoint) while desktop falls back to static `16px`
  body with per-element px/rem sizing — the vw-scaling trick is mobile-only
  here, not a universal fluid-type system.
- Display type carries **systematic negative letter-spacing**, roughly
  -3% to -4% of font-size, tightening further as size increases (e.g.
  -11.2px tracking on a 280px desktop headline = -4.0%).
- Type scale is **deliberately not a clean modular ratio** — desktop steps
  16→21.3→38→70→280px are irregular, jumping ×4.0 for the hero-scale
  element. Reads as hand-tuned for maximalist impact, a legible break from
  "safe" scale discipline rather than an oversight.
- **No container max-width anywhere, at any breakpoint** — the root
  `.l-container` computes `max-width: none` at both 375px and 1440px.
  Genuinely edge-to-edge at every size; the one exception is a single
  350px-fixed card-grid wrapper. Matches the site's maximalist, unbounded
  energy — the opposite of the "centered 1200px column" default.
- Section `padding-block`/gaps are hand-tuned per section with no visible
  formula (mobile→desktop scale factors range 2.36×–3.4×, none matching
  the 3.84× viewport-width ratio).

### Named techniques

- *Emoji-as-typography*: emoji (💛⚔️🗡️💖✨🌺) inline inside headline copy and
  scattered as standalone rhythm/punctuation between marquee words — not
  illustrative, purely textural.
- *Multi-band marquee ticker*: several independent horizontal scrolling
  text bands, different speeds/colors, bleeding off both viewport edges;
  mixes real information (tech-stack names: Unity/Unreal
  Engine/AWS/C++/Kotlin/COBOL/Swift/Scala) with kaomoji decoration — reads
  as informational *and* decorative at once.
- *Bordered message-note boxes*: individual short phrases each in a
  thin-border rectangle, floating at independent positions/sizes over a
  busy background — the one hierarchy device that keeps a dense page
  readable.
- *Photo-stack collage, now confirmed as a cheap static trick, not a
  filter*: the hero "shutter" photo effect is **one single source image
  stretched to four different aspect ratios and layered** (`object-fit:
  fill` on 4 copies of the same file at cascading sizes: 1.798 / 1.587 /
  1.434 / 1.315 aspect ratios) — a fanned/sheared depth illusion built from
  one asset with pure CSS sizing, no WebGL, no real photo burst-sequence.
- *Multi-fill headline*: within one headline, each word gets a different
  treatment (solid black / solid orange-red / blue-to-teal gradient)
  instead of uniform color.
- Real neighborhood names (TOMIGAYA/HARAJYUKU/SHIBUYA) and real street
  photography woven into the ticker — the loudest of the three sites is
  still subject-grounded, not generic chaos.

### 写真のサイズ・挙動・演出 (photo sizing/motion/staging)

- **Every real photo uses `object-fit: fill`, not `cover`** — confirmed via
  computed style on every sampled image. This only works safely because
  every container is pre-sized to match that exact image's natural aspect
  ratio (aspect ratios matched to 3+ decimal places in testing) — `fill`
  with a mismatched container would visibly distort. A viable alternative
  to `cover`-and-crop *if* you control the container size precisely per
  image, at the cost of losing `cover`'s safety net against future content
  changes.
- **Live scroll-into-view test (screenshotted before/after) found zero
  actual photo motion** — no hover transform, no scroll-triggered reveal
  rule exists anywhere in the stylesheet (confirmed by scanning every
  `:hover`/`is-inview`/`is-active` rule). What looks like it might be
  reactive is a **canvas-repaint density artifact**: the ticker canvases
  render sparser after being scrolled out and back in, unrelated to the
  photos. All of this site's kinetic energy is in the canvas ticker layer
  and character-sprite transforms, none of it in photo behavior — an
  honest negative finding, not an assumption.

### 文章 (copywriting patterns)

- **EN hook + JP poetic gloss, not literal translation** — the English
  headline fragment is paired with a Japanese line that freely
  reinterprets rather than translates it (e.g. the JP line under the hero
  reads roughly "unleash your potential," a paraphrase, not a translation).
  The same EN-hook/JP-gloss pairing repeats at section level.
- The "MESSAGE" section is a cascading monologue in **casual/blunt
  register** — plain da-form sentence endings, not polite です/ます — closing
  on a direct rhetorical question rather than a declarative CTA. Short,
  punchy fragments (5–15 characters each).
- CTAs are terse English exclamations/labels, not sentences: "JOIN US.",
  "SAY HELLO!", "MORE", "PLAY" — bare imperatives styled like the eyebrow
  labels, not button-with-sentence.

### 情報設計・説明構造 (information architecture, checked against the actual DOM section order)

The real, measured section sequence (not a summary — the literal order a
visitor scrolls through): hero manifesto → "OUR IDENTITY" label → the
10-line MESSAGE monologue → "CREATORS" → individual "CAREER JOURNEYS"
(named-person career-story profiles) → "CULTURE JOURNEYS" (event content)
→ "CULTURES" → "ORIGINALS" (a named list of real client brands/projects:
SLIT, QR81V, COSMOS JUICE TOMIGAYA, VAULTROOM, ZEY, 亀レON, SHARED OFFICE,
each with a release year) → 4 rational value-prop cards (diverse
clients/projects, diverse work styles, career-change support, professional
skill backup) → "JOIN US" → "SAY HELLO!" contact.

The load-bearing pattern: **identity and culture come first, real-work
proof comes second, rational HR benefits come last, and the practical
"we're hiring, here's what you get" content is saved for after the
emotional hook has already landed.** A recruitment site for creative talent
is not solved like a product landing page — the visitor's actual decision
("do I want to *be* these people") is emotional/identity-driven before it's
rational, and the section order mirrors that decision process instead of
leading with a benefits list. The named-brand portfolio ("ORIGINALS") does
double duty as both social proof *and* an aspirational preview of what a
hire would actually get to work on — proof framed as opportunity, not as a
credentials list.

### Related site: zoccon.me (parent corporate site)

A genuinely different site, not a redirect — and a **different design
language entirely**, not the same system reused. Title translates to "ON
Co., Ltd. — a creative company in Harajuku, Tokyo." Only 2 `<canvas>`
elements (vs. 52 on the recruit microsite). Hero: one full-bleed moody
backlit photo, hollow-outline + solid-fill condensed display type, generous
negative space, a simple "SCROLL" cue — minimal, cinematic, editorial.
Read: the corporate site targets clients with a calm, serious register; the
recruit microsite's maximalist chaos is a deliberate register-shift aimed at
attracting young creative-industry candidates — same company, two
audience-driven design systems. One concrete inconsistency caught in
passing: the recruit subdomain's meta description says the company is based
in **Shibuya**; the parent site's says **Harajuku**.

## quoitworks.com (株式会社クオートワークス — Web制作会社コーポレートサイト, checked 2026-08-02)

### Stack, color, structure

- WordPress (All in One SEO / AIOSEO 4.9.10) + jQuery 3.7.1 + custom
  vanilla `loading.js`/`main.js` + **Splide.js** for carousels (confirmed
  via live DOM classes — `splide--loop`, slide-clone elements for infinite
  loop, `is-active`/`is-next`/`is-prev` state classes) + `a3-lazy-load`
  plugin (server side) wrapping `jquery.lazyloadxt` (client engine). No
  GSAP/Three/Locomotive. Proof point: high production value does not
  require a heavy animation framework — Splide plus hand-written CSS
  transitions carries the whole motion system.
- Full-asset-preload gate: a deliberate preloader (visible progress bar,
  ~15–25s on a typical connection) blocks first paint until above-the-fold
  and portfolio images are fetched. Precise mechanism: `body` cycles
  `is-loading`/`is-reveal-wait` (cursor: wait) → `is-reveal-preparing`
  (`#wrapper { opacity: 0.01 !important }`) → `is-loaded`. Deliberate
  trade-off: slower time-to-first-paint for zero scroll-jank once the
  visitor starts interacting with the carousel. Right call for a
  portfolio-heavy agency site, wrong call for a conversion-sensitive
  landing page.
- Fonts served via **TypeSquare** (paid JP webfont subscription) — AXIS
  Font ベーシック across **three distinct weights used structurally**:
  extra-light (EL) for all body copy, light (L) for all headings/nav,
  regular (R) only for nav labels — **no weight above "light" appears
  anywhere sampled**; emphasis comes from size/color, never boldness. Plus
  YakuHanJP for JP/Latin punctuation-width correction, and Montserrat for
  Latin display/numeral text (hero English headline, `<time>` captions).
- Color: warm near-blacks `#1F1C1C`/`#2B2325` and warm off-whites
  `#F7F5F5`/`#F2EBEC` as the section-background rhythm; muted bronze/gold
  accent `#937B55` for buttons; even the "neutral" grays lean warm — body
  text `#333232`, caption text `#B3AFB0` (a muted mauve-gray, not true
  gray).
- Structure: 61 SVG, 18 fixed-position elements, 0 sticky, 1 video, 0
  canvas. 154 total `<img>`, **100% carry an `alt` attribute** — but 97 are
  correctly `alt=""` for decorative/clone images (68 of the 154 are
  Splide's own infinite-loop clones), leaving 38.4% of *real* images with
  descriptive alt. This is the WCAG-correct pattern, not an oversight —
  contrast with recruit.zoccon.me's 10.2%, which is a real gap not a
  decorative-image artifact.

### SEO

- Clean three-way split that's easy to get wrong: the semantic `<h1>` is a
  keyword-rich string sitting in a properly `.u-visually-hidden` span
  inside the header logo link; the *visible* logo mark is an inline SVG;
  the big hero copy ("We Create / Useful And Beautiful…") is a plain
  `<p>`, entirely decoupled from both. Three roles, three separate
  elements, none faking another.
- 11 H2s, one per major section, no keyword stuffing in H3s (case-study
  and blog-post titles are plainly descriptive).
- **JSON-LD present** (the only one of the three reference sites that has
  any): `Organization` (phone, logo, `sameAs` to social profiles,
  `alternateName` for the English brand form, `knowsAbout`, `subjectOf`
  linking their own media properties), `Person` (representative, with
  `jobTitle`), `WebPage`, `WebSite`, `BreadcrumbList`. No `Service`/
  `LocalBusiness`/`Product` schema.
- `robots.txt` (200, 210 bytes) carries the emerging **`Content-Signal:
  ai-train=no, search=yes, ai-input=yes`** directive — see the LLMO/AIO
  section below for what this actually does and its current real-world
  effect (spoiler: less than the directive's sophistication implies today).
- `/sitemap.xml` → 200, proper AIOSEO sitemap index; bonus `/sitemap.rss` →
  200, a full RSS 2.0 mirror of the sitemap (AIOSEO feature, rare in the
  wild).
- `/llms.txt` → 200; see the LLMO/AIO section for the full teardown of why
  this is the best-executed example found in this research.

### 文字スタイル (typographic scale)

Root `<html>` font-size ≈ 10px at both breakpoints (barely-perceptible
~0.1% fluid nudge on mobile) — unlike recruit.zoccon.me's vw-driven root,
individual elements here get their own explicit per-breakpoint values, not
a rescaled root.

| Element | Mobile | Desktop | Notes |
|---|---|---|---|
| Hero EN headline | **25px** / 1.4 lh | **19px** / 1.33 lh | Montserrat — larger on *mobile*, deliberately smaller on desktop |
| H2 eyebrow label | 10px / 1.0 lh | 18px / 1.0 lh | AXIS L, 3% letter-spacing |
| Body `<p>` | 12px / 21px lh (1.75) | 14px / 28px lh (**2.0**) | AXIS **EL** (extra-light) |
| `<time>` caption | 8px / 1.5 lh | 10px / **2.0** lh | Montserrat |

Notable, counter-intuitive finding: **the hero headline is larger on
mobile than desktop** — on mobile it's the dominant stacked element and
needs presence; on desktop it deliberately stays small since it shares the
fold with the case-study carousel, reinforcing the restrained "quiet
luxury" read rather than shouting. No single geometric type-scale ratio;
instead a clear **three-tier line-height system** (1.0 tight for
single-line labels / 1.33–1.56 for headline-scale text / 2.0 airy for body
and captions). Every JP element uses `letter-spacing: 3%` (a *percentage*,
scaling with size) uniformly; every Latin/Montserrat element instead uses a
fixed `0.5px`.

**Correction to an earlier draft of this entry**: the giant "USEFUL AND
BEAUTIFUL" background text was first described as "outline/stroke-only"
from a visual read. Direct inspection of `.c-maskText-inner` found
`webkitTextStrokeWidth: 0` and a **solid** fill — the outline *look* is an
illusion produced by extreme **`font-weight: 100`** (hairline) at huge
scale (190px mobile / 430.7px desktop, Montserrat), not an actual
stroke/mask/clip-path. It also isn't static: `animation: 300s linear
infinite` drifts it horizontally, continuously, almost imperceptibly
slowly. Lesson for this skill generally: an extreme-hairline weight at
large size reads as "outlined" to the eye even with a fully solid fill —
cheaper and more robust (no stroke-rendering quirks across browsers) than
an actual `-webkit-text-stroke` approach.

### 空間の使い方 (spacing/rhythm)

Two-tier centered container system (at 1440px, 1425px effective after
scrollbar): narrow/text tier (`.c-top-inner`, philosophy/service/works)
**max-width 900px**; wide/grid tier (`.c-grid-outer`, feature/testimonial
grids) **max-width 1200px**. Mobile: flat **15px** side margins (345px
content in a 375px viewport). The hero headline column runs its own
distinct 575px-wide grid, not the 900/1200 system.

Section `padding-block` is real and section-specific, not a repeated
constant — e.g. the philosophy section carries **900px top padding on
desktop**, which is not decorative whitespace but the scroll-pin dwell
distance for a `js-stickySection` (confirmed via the class name, not
guessed). **Inter-section gaps are ~0** — breathing room comes entirely
from each section's own padding-block; section transitions are handled by
abutting background-color changes, not margin gaps.

### 写真のサイズ・挙動・演出 (photo sizing/motion/staging)

- Portfolio/testimonial images: WordPress auto-generated `*-scaled.jpg`
  (natural ~2560px wide, WP's big-image-size threshold), `object-fit:
  cover`, clean fixed aspect-ratios (`252/190` cards, `374×199`
  testimonials).
- Real confirmed hover effects, verified by reading all 15 `:hover` rules
  in the live stylesheet rather than guessing: (1) a service key-visual
  `clip-path` reveal with **asymmetric easing** — snappier in
  (`cubic-bezier(0.43,0.05,0.17,1)`, 0.2s) than out (0.3s); (2) a
  custom-cursor "stalker" label that follows the pointer and reads "実績を
  見る" over the works slider; (3) a modern CSS `:has()`-based slider-peek
  — hovering the next/prev buttons shifts the track `±5rem` as a hint, no
  JS listener needed.
- **No portfolio-image hover-zoom could be confirmed** despite a plausible
  `transition: transform` on the `<img>` itself — no matching `:hover`
  rule targets it in the full stylesheet scan; that transition most likely
  smooths Splide's own drag/position transform, not a decorative zoom.
  Reported as unconfirmed rather than invented, on purpose.
- No `is-inview`/scroll-triggered photo-reveal class pattern found in the
  stylesheet (10 `is-inview`-adjacent rules exist but all tie to the
  preload-reveal gate and link underlines, none to photos).

### 文章 (copywriting patterns)

Measured, low-affect B2B register — the opposite pole from
recruit.zoccon.me. Hero EN headline is one full declarative sentence, zero
exclamation/slang. JP subhead is a single polite です/ます comma-chain. Across
the full sampled corpus, **exactly one exclamation mark was found** ("問い
合わせの質が向上！" on a testimonial headline) — everything else is
period-terminated or a clean sentence fragment. No emoji, no urgency
language ("今すぐ"), no ALL-CAPS shouting. The restraint itself functions as
the brand signal, consistent with the visual "quiet luxury" read.

### 情報設計・説明構造 (information architecture, checked against the actual DOM section order)

The real, measured section sequence: hero mission statement + portfolio
carousel → **経営理念** (philosophy, with a large "USEFUL AND BEAUTIFUL"
statement) → **課題解決** (problem-solving — not "our services," but real
pain-point headlines phrased as the visitor's own question: "サイト改善で
優先すべき費用対効果が高い9つの場所とは？", "Webサイトから問い合わせと商談に
つなげる8個の正攻法とは", "古く見えるサイトを、現代的に整える6個のヒント")
→ **サービス紹介**, organized into exactly 3 named categories (問題解決指向の
Web制作: 6 site types · ブランディング支援: 5 items · マーケティング・集客
支援: 7 items, including SEO・LLMO/AIO) → **制作実績**, introduced by a
third-party-validation line ("多くのメディアや書籍で優良事例として実績が
取り上げられています") before the portfolio grid itself → **お客様の声**,
5 named case studies with outcome-framed headlines → **私たちの特徴**, 6
differentiator statements plus 4 hard numeric credibility stats (54件実績 /
32件専門誌掲載 / 19件アワード受賞 / 25件登壇) → **ご発注ガイド**, an explicit
FAQ/objection-handling block ("どのような仕事の進め方をするのか知りたい",
料金, 発注前チェック, よくある質問, デザイン依頼で伝えると良いこと) →
**仕事の進め方**, a named 5-step process (調査→戦略→詳細設計→デザイン・
実装→運用・改善) with a 1-2 sentence description per step → **お知らせ**
(more third-party media/award mentions, dated for freshness) →
**運営ブログ** (thought-leadership content) → **運営メディア一覧** (their
own YouTube show/design-gallery site/social accounts, expanding their
authority footprint) → **採用情報**, kept brief, at the very end.

The load-bearing pattern — this is the "求心力" (drawing/binding power)
mechanism, made concrete: **every section targets one specific class of
visitor doubt, in the order a skeptical B2B buyer actually raises them**
(what's wrong with my current site? → what exactly do you offer, grouped
so I can find my need? → have real companies actually used you? → what did
they say? → are you legitimate at scale? → what will working with you
concretely involve, and what if I have questions before committing? →
what does the engagement actually look like week to week? → are you
credible beyond your own claims about yourself?). Nothing is "selling" in
the pushy sense — pain-point content, categorized services, social proof,
hard numbers, an FAQ, and a demystified process are five *structurally
different kinds of trust signal*, stacked so a buyer's confidence compounds
across the scroll instead of resting on one claim ("we're the best")
repeated in different words. Compare directly against recruit.zoccon.me's
sequence above: same underlying discipline (order sections by the
visitor's actual decision process), opposite order, because a rational B2B
purchase decision and an emotional "do I want to work here" decision are
different decision types — see Cross-site synthesis for the general rule
this implies.

### Related site: AZA Corporation (aza.co.jp)

Followed the real outbound case-study link. AZA is an event AV/ICT
production company — a **visibly different design language**: huge bold
condensed uppercase type, a saturated crimson accent (vs. quoitworks' own
muted bronze `#937B55`), diagonal-textured dark-maroon panels, a
gray dot-grid background — bolder and more "technical/masculine-coded" for
an events company, clearly client-brief-driven rather than a quoitworks
reskin. One structural pattern **did** carry over: AZA also has
quoitworks' signature persistent bottom tab-bar nav — evidence this is a
reusable quoitworks UX pattern deployed across client deliverables, not
just self-referential branding.

### Read (overall)

Restrained editorial "quiet luxury" — warm-neutral (not stark) darks/lights,
one muted accent, generous negative space, near-zero exclamation marks in
the copy — while technically rich underneath (JSON-LD, Content-Signal,
hand-authored llms.txt, Splide-driven carousels with genuine CSS `:has()`
interaction, a structural three-weight type system). Of the two axes this
file checks per site, quoitworks is the only one of the three that scores
well on *both* visual restraint-with-craft and machine-readability
(JSON-LD, Content-Signal, llms.txt) at once — the other two each lead on
one axis while showing real gaps on the other (see Cross-site synthesis
item 8).

## sirup.online/5th/ (SIRUP 5th Anniversary Special Site — 音楽アーティスト記念特設, checked 2026-08-02)

### Stack, color, structure

- **GSAP 3.12.2 + ScrollTrigger** — a repeatedly-observed stack choice for
  this class of high-motion scrollytelling site, not unique to this one
  entry (see Cross-site synthesis) — + **LocomotiveScroll** + **THREE.js**
  with 2 `<canvas>` elements (576×1440 internal buffer each, deliberately
  oversized and clipped) + Adobe **Typekit**.
- **Critical tooling finding, generalizes beyond this one site**: this
  site's LocomotiveScroll setup suppresses native scrolling entirely —
  `window.scrollY` stays pinned at `0` regardless of `window.scrollTo()`
  calls, while the real scroll position lives in a CSS `transform:
  translateY()` on `[data-scroll-container]`. A screenshot taken right
  after `scrollTo()` on this site is **not trustworthy** — it silently
  shows the untouched top-of-page frame while looking like the call
  succeeded. Confirmed directly: `scrollTo(0, 3200)` left the container
  transform at `matrix(1,0,0,1,0,0)` (unmoved) while `window.scrollY`
  falsely reported `3200`. Real wheel/touch input moves the transform
  correctly. **General rule for this skill: whenever a site's scroll feels
  custom (inertia, parallax, pinned 3D), verify state with real input
  simulation, never `scrollTo()` alone** — a scroll-jacking library can make
  a real regression invisible to `scrollTo()`-based testing (or, as here,
  make working scroll look broken to the checker), the same root failure
  mode as any CSS-level scroll-container mistake, just a different cause.
- With real scrolling, the WebGL canvas gradient/glow **genuinely shifts
  color with scroll position** (cool navy near the hero top → warm
  red/orange further down) — confirmed real scroll-linked motion through a
  tall painted field, not a static per-section image swap.
- Color: pure black `#000000` base; saturated warm sunset gradient (deep
  red/maroon → orange → glowing warm halo) driving the hero, cooling to
  dark navy/mauve later — a genuine color-script arc: the palette itself
  carries the emotional arc of the page, the way a film's color script
  plans mood through color progression rather than through plot alone
  (executed here via WebGL rather than static art). The two measured UI
  accent colors (`#5262BE`, `#4554AC`, on date-stamp and numeral
  micro-copy) are cool blue-violet — the arc's "cooling" extends into UI
  chrome, not just background.

### SEO

- Meta description and complete OGP/Twitter Card tags present, but **no
  `<link rel="canonical">` at all** — a real gap for a page that could
  plausibly be reached via multiple query-string variants.
- **Zero JSON-LD** — no MusicGroup/Event/CreativeWork schema despite being
  a campaign microsite for a real touring artist, a missed opportunity
  relative to quoitworks' example.
- **Heading structure is close to non-existent**: only two `<h1>` elements
  exist on the entire page, and **zero h2–h6 anywhere** (953 real text
  nodes scanned). Both H1s carry **empty live text** — their content is
  images (`<h1><img alt="SIRUP"></h1>` for the logo; the hero headline H1
  wraps a `<picture>` whose `alt` attribute carries the entire visible
  headline, alt text that itself contains a **production typo**,
  "ANNIVARSARY" instead of "ANNIVERSARY," baked into a live asset).
  Section labels that look like headings (ABOUT, ROOTS, ROOTS PLAYLIST) are
  unmarked `<img>`/`<div>`, not semantic heading elements at all.
- Image alt coverage: 357 `<img>`, **89.6%** carry non-empty alt — much
  stronger than the near-zero heading structure would suggest, and
  stronger than recruit.zoccon.me's 10.2%.
- `robots.txt` and `sitemap.xml` under `/5th/` are a genuine **soft-404
  trap**: both return **HTTP 200** but the response body is literally the
  SPA's own `index.html` — worse than a real 404, because it looks
  successful to an automated check while delivering garbage to any crawler
  or monitor. (The domain root, separately, is a real WordPress install
  under `/wp/` with its own working sitemap — the campaign microsite under
  `/5th/` is the part with the gap.)

### 文字スタイル (typographic scale)

The most important finding here isn't a number, it's a category error to
avoid: **almost none of this site's visible display type is live CSS
text** — it's pre-rendered SVG/webp image assets. The hero headline alone
is built from **24 stacked SVG layers** (`header-title-1.svg` …
`header-title-24.svg`), only the first carrying real alt text, the rest
`alt=""` decorative, matching per-layer 3D parallax data-attributes — a
pseudo-3D layered illustration standing in for a headline, not typography
in the CSS sense at all. Treat "look at this site's headline type" requests
as an illustration/export question, not a font-stack question.

Real, live DOM text with computed CSS styles exists in exactly three
places, and — deliberately — each uses a **different font family**:

| Element | Size (375px vp) | Line-height | Letter-spacing | Family |
|---|---|---|---|---|
| Body paragraph | 10.87px | 19.02px (1.75) | 0.54px | source-han-sans-japanese |
| Date stamp ("2017.09.27") | 15.40px | 1.0 (no leading) | normal | Termina (Typekit) |
| Numbered index ("01") | 14.49px | 1.5 | **2.90px (~20% of size)** | Glodok (Typekit) |

Three distinct typefaces for three distinct kinds of functional micro-copy,
none of which appears in the illustrated headline system at all — a
genuinely three-way typographic system hiding underneath what looks like a
single collage aesthetic.

### 空間の使い方 (spacing/rhythm)

Almost every container checked computes `padding: 0` / `margin: 0` — the
vertical rhythm is **not built from CSS box-model spacing** at all; it
comes from cumulative document flow plus absolutely-positioned decorative
layers driven by the animation library's own `data-insert`/`data-parallax`
attributes. Real evidence is scroll-position deltas: hero→"ROOTS" heading
0→711px; ROOTS→playlist CTA 186px; header block ends exactly where `<main>`
(the 3D discography stack) begins (1267.7px ≈ 1268px, a clean match);
`<main>` itself spans 3465px; `<main>`→footer 113px; footer 725px (total
page height 5571px, confirming the figure from the first research pass).
Gaps between major beats (113–280px) are tight relative to their own
section heights, and much of the apparent spacing is really **layout
overlap** — text sitting directly on top of photography — consistent with
the poster/zine framing already established, not whitespace-driven at all.

**Desktop framing, now with exact numbers**: at 1440px, the content column
is a precise **918px wide, offset 261px from the left edge** — symmetric
261+918+261=1440 gutters. 918/375 ≈ 2.45× the mobile reference width: this
confirms, with real pixel values, what looked like a "mobile card floating
in a void" from the earlier visual pass — it's a scaled-up mobile card
inside fixed black gutters, not a reflowed desktop grid.

### 写真のサイズ・挙動・演出 (photo sizing/motion/staging)

- Hero portrait (`header-artist.webp`): rendered 330.6×873.75px, aspect
  ratio **0.378** (~1:2.64, a very tall/narrow bespoke crop), `object-fit:
  fill` on a purpose-exported asset, not a generically-fitted responsive
  image. A separate `header-panel.webp` sits behind it as the arch/halo
  mask layer.
- **Scroll-scrubbed 3D confirmed, not autoplay** — this required real wheel
  input (see the LocomotiveScroll finding above). A small 5-tick wheel
  nudge moved the scroll-container transform by exactly 24px and produced
  a legitimate mid-rotation intermediate frame (two collab-track "spines"
  visible on both their front and angled side faces simultaneously) —
  direct evidence of continuous scroll-to-3D-rotation binding, resolving
  what was an open question in the first research pass.
- Resizing the viewport triggers a visible ~1–2s re-layout where content
  briefly renders at the wrong scale before snapping to final position —
  worth a settle-wait if scripting future automated screenshots of this
  site.

### 文章 (copywriting patterns)

Register is intimate and narrative — closer to liner notes than marketing
copy. The bio paragraph opens mid-fact (debut year, catalog count) rather
than with a pitch-style hook. A genuinely deliberate content-density
strategy was found: the site serves **two different lengths of the same
paragraph** — a fuller desktop version naming specific collaborators/brand
tie-ins, and a trimmed mobile sibling that omits them — not just a
responsive reflow of identical text, an actual per-breakpoint editorial
decision. Section intros stay descriptive/curatorial ("a playlist that
helps you understand the artist's range of collaborators") rather than
imperative ("Listen now!").

### Related site: sirup.online/ (root, the artist's baseline site)

Materially simpler than the anniversary microsite: **zero `<canvas>`
elements**, no GSAP/THREE/Locomotive detected, a plain dot-pagination image
carousel, a conventional NEWS/LIVE/MEDIA tab bar. Also a real, notable SEO
gap the anniversary microsite doesn't have: **no meta description and no
`og:image`** on the homepage. Confirms the WebGL/GSAP/collage-typography
treatment is exclusive to the campaign microsite, not the artist brand's
day-to-day baseline — a "flagship demo, not the everyday product" pattern
worth remembering when scoping how much technical investment a one-off
campaign page can justify versus the always-on site.

### Read (overall)

The highest technical ceiling of the three (only one using WebGL), a
dramatic sunset-to-night color arc extending into UI accent colors, a real
three-way typographic system hidden under an almost-entirely-illustrated
headline layer, and the most unconventional desktop-layout philosophy of
the set — alongside the weakest semantic-heading structure and the
soft-404 sitemap/robots gap of the three. Visual ambition and technical
SEO/accessibility hygiene are not the same axis; this site is proof in
both directions at once.

**A fourth topic was researched alongside these three sites' visuals but
lives in its own file**: `references/llmo-aio-evidence.md` covers
LLMO/AIO (AI-search citation optimization) — real vendors, academic
research, platform statements, and a contested/debunked-vs-confirmed
evidence table. Load it when a brief's scope includes search/AI-answer
visibility; skip it for a purely visual Pass-1 plan.

## Cross-site synthesis (what's actually transferable)

1. **GSAP + ScrollTrigger is, again, the framework reached for when a site's
   ambition is high-motion scrollytelling** — only 1 of the 3 sites in
   *this specific file* uses it (SIRUP), so call it a repeatedly-observed
   option for that class of brief, not a universal default; recruit.zoccon
   and quoitworks both prove real production value is reachable without it.
   **Splide.js is a confirmed real-world carousel choice** (quoitworks)
   alongside hand-rolled solutions — legitimate when a project needs a
   carousel without GSAP's full weight.
2. **LocomotiveScroll is a real-world-deployed smooth-scroll option**,
   worth knowing about whenever a brief calls for inertia/parallax scroll.
   **Whenever a site uses any scroll-jacking library, verify motion with
   real wheel/touch input, never `window.scrollTo()`** — on sirup.online
   (this file, SIRUP's "Stack, color, structure" entry), `scrollTo()`
   silently *looked* like it worked while the real scroll-container
   transform never moved — the general failure mode is that a
   `scrollTo()`-based check can't be trusted to reflect what a real user's
   scroll gesture actually does, regardless of whether the underlying cause
   is a scroll-jacking library or a CSS overflow mistake.
3. **Premium JP webfont services (TypeSquare, Adobe Typekit) show up in 2
   of 3 sites**, and quoitworks specifically shows a *three-weight
   structural system* (extra-light body / light headings / regular nav,
   nothing bolder) rather than just "a nicer font" — free Google Fonts is
   fine for body copy everywhere, but the "this feels expensive" signal at
   this tier comes from a licensed *display* face used with real weight
   discipline, not from replacing the whole stack.
4. **An extreme-hairline weight (100) at huge size reads as "outlined" even
   with a fully solid fill** (quoitworks' "USEFUL AND BEAUTIFUL") —
   cheaper and more cross-browser-robust than an actual
   `-webkit-text-stroke` for the same visual effect.
5. **Four genuinely different valid poles, not one style to copy**:
   maximalist collage/ticker/emoji energy (recruit.zoccon) · restrained
   warm-neutral editorial luxury (quoitworks) · dramatic WebGL-driven
   color-arc drama (sirup) · and each site's own *parent/baseline* site
   proved to sit at a calmer point than its flagship campaign page
   (zoccon.me vs. the recruit microsite; sirup.online root vs. `/5th/`) —
   a flagship/campaign page can justify a technical/visual ceiling the
   always-on brand site doesn't need to sustain. None of the four poles is
   more "correct" — this skill's Core stance (ground every choice in the
   subject) decides which fits a future brief.
6. **When a site has a signature technique, it's reused as a running motif
   within that site, never a one-off**: recruit.zoccon's bordered
   message-boxes recur across its whole "MESSAGE" section; quoitworks'
   persistent tab-bar is confirmed reused across its own site *and* its
   client AZA's (a portable agency signature, not just self-branding);
   SIRUP's rotated micro-labels and arch-halo framing each repeat at
   multiple points in that one site. Independently confirms this skill's
   existing "Signature: one memorable element... everything else stays
   disciplined" rule — even the maximalist site has real discipline in
   *how* it repeats its noise.
7. **Desktop is never just mobile stretched wide, at least in the two
   sites where a clear desktop re-composition was actually measured**:
   quoitworks re-composes asymmetrically (headline+carousel split, and the
   hero headline actually gets *smaller* on desktop, not bigger); sirup
   frames the mobile-proportioned card in a precisely-measured 261px void
   on each side. recruit.zoccon.me's desktop behaves more like a direct
   scale-up (edge-to-edge `max-width:none` at both breakpoints, same
   layout logic) — so treat "recompose, don't just scale" as the
   higher-ambition option proven twice here, not something every site in
   this set demonstrates.
8. **Visual polish and machine-readability are independent axes**: the
   most visually restrained site (quoitworks) also has the most
   sophisticated LLMO/AIO layer (see `references/llmo-aio-evidence.md`);
   the most visually loud site (recruit.zoccon) is SEO-baseline but has
   the weakest alt-text coverage (10.2%) of the three; the most technically
   advanced site (sirup, WebGL) has both the weakest heading structure
   (zero h2-h6) and a soft-404 sitemap. A beautiful front-end implies
   nothing about either axis — check both independently.
9. **Information architecture should be ordered by the visitor's actual
   decision type, not by a template section order** — the clearest,
   highest-value lesson from comparing recruit.zoccon.me's and
   quoitworks.com's real DOM section sequences (see each site's own 情報設計
   entry above) side by side. quoitworks sells a considered B2B service to
   buyers who need rational justification: its order is pain-point →
   categorized offering → social proof → hard numbers → FAQ → demystified
   process, each section clearing one specific class of doubt before the
   next. recruit.zoccon recruits creative talent, whose decision is
   identity-driven before it's rational: its order is manifesto → culture →
   named-person stories → real-work-as-aspiration → *then* rational HR
   benefits → CTA. Same underlying discipline (sequence mirrors how this
   specific visitor actually decides), completely different concrete
   order — before writing a Pass-1 layout section, name the decision type
   the target visitor is actually making, then sequence sections to answer
   objections in the order that decision naturally raises them, instead of
   defaulting to a generic hero→about→services→contact template.

## Appendix: a research-operations note, not design guidance

Logged for whoever re-runs this kind of multi-agent research, not for
generation-side design decisions: during the 2026-08-02 research pass, two
independently-dispatched research agents — one working on
recruit.zoccon.me, one working on quoitworks.com — each separately
reported their browser tab navigating, unprompted, to one of the *other*
two sites in this exact set. On its own this would read as a compromised
ad slot on a real business's site; but because it happened to **two
different sites, cross-linking to each other's exact set**, while
**multiple research agents shared one browser-automation pane
concurrently**, the far more likely explanation is tab-ID contention
between concurrent agents in the shared pane, not a genuine compromise on
any of these three real, unrelated businesses. Recorded honestly as
unresolved rather than either alarmed or ignored. If re-running
multi-agent browser research, prefer one agent at a time per shared
browser pane, or confirm each agent gets its own isolated pane.
