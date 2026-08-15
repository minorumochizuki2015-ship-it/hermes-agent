# Japanese Web Typography

Rules for surfaces containing Japanese text. Violations here are what make a
page look "off" to native readers even when the layout is technically fine.

## Font selection and pairing

- Choose the JP face first, then the Latin face to harmonize — not the reverse.
  JP glyphs dominate the visual texture.
- Dependable JP stacks (self-host or Google Fonts):
  - Modern/neutral: "Noto Sans JP", "Zen Kaku Gothic New", "M PLUS 1p"
  - Warm/humanist: "Zen Maru Gothic", "Kiwi Maru" (sparingly, small sizes suffer)
  - Editorial/high-end: "Noto Serif JP", "Zen Old Mincho", "Shippori Mincho"
  - Display/brand: "Zen Antique", "Reggae One", "Dela Gothic One" (headline only)
- Always end stacks with system fallbacks:
  `..., "Hiragino Kaku Gothic ProN", "Hiragino Sans", Meiryo, sans-serif`
  (or Mincho equivalents for serif).
- Mixed JP/EN: Latin companion should match the JP face's weight and mood
  (e.g. Noto Serif JP + Cormorant/EB Garamond; Noto Sans JP + Figtree/DM Sans).
  Set `font-feature-settings: "palt"` only for display text, never body.

## Composition

- Body: 15-16px minimum, line-height 1.7-2.0, max line length ~38-42 JP chars.
- Headings: line-height 1.3-1.5; JP needs more than Latin at the same size.
- **Never negative letter-spacing on JP text.** Display JP may take
  `letter-spacing: 0.02-0.08em`; body stays 0 to 0.05em.
- Line breaks in headings must not split words/names unnaturally
  (「温泉ス/パ」). Control with `<wbr>`, `&#8203;`-free spans,
  `word-break: keep-all` + manual breaks, or `text-wrap: balance`.
- 禁則処理: rely on browser defaults (`line-break: strict` for tight columns);
  never disable wrapping such that punctuation starts a line (、。」が行頭).
- Numerals and units: use half-width digits inside JP text; add thin spacing
  around Latin words with `text-autospace` where supported, or accept default.
- Vertical text (`writing-mode: vertical-rl`) is a powerful high-end signature
  for 和 brands — use for short labels/headings only, test on mobile.

## Common failure smells (check in screenshots)

- JP rendering in default Mincho when the design intends gothic → font stack
  or loading failure.
- Tofu (□) or mixed-face lines → subset/unicode-range mistake.
- Buttons sized for English labels clipping JP (「お問い合わせはこちら」).
- `text-transform: uppercase` styling applied to romaji brand names where the
  brand uses lowercase (onsen SPA ≠ ONSEN SPA) — brand notation is source truth.
- Justified JP body (`text-align: justify`) creating uneven rivers on narrow
  mobile columns.
