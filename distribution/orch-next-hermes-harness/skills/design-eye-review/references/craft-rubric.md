# Craft Rubric — scoring like a design director

Score each axis 0-4 from the screenshots. Anchors below. Justify in one line
that names concrete elements — see few-shot examples at the bottom for the
required specificity. Vague praise ("looks clean") is a rubric violation.

## Anchors (apply to every axis)

- **0 — Broken**: the axis actively damages usability or trust.
- **1 — Amateur**: a non-designer would notice something is off.
- **2 — Acceptable/generic**: nothing wrong, nothing memorable. Template feel.
- **3 — Professional**: deliberate, consistent, would pass a design team review.
- **4 — Distinctive**: memorable and clearly owned; could not be confused with
  a competitor's site.

## Axes

1. **Layout & hierarchy** — one clear focal point per screen; size/weight/
   position encode importance; the eye path matches the intended reading order;
   sections have rhythm rather than uniform stacked boxes.
2. **Typography** — deliberate display/body pairing; consistent scale (not 9
   near-identical sizes); JP/EN mixed text harmonized; line length and
   line-height comfortable; weight used for hierarchy, not decoration.
3. **Color** — a palette, not accumulated hexes; accent color reserved for
   action/emphasis; neutrals do the work; contrast supports reading;
   dark surfaces avoid pure-black/pure-white harshness.
4. **Spacing & alignment** — visible grid discipline; consistent spacing scale
   (multiples, e.g. 8px); related items closer than unrelated (proximity);
   breathing room around the most important elements.
5. **Imagery & assets** — photos/illustrations look intentional and on-brand;
   consistent treatment (crop, tone, radius); real subject visible when the
   business is a real place/product; icon style unified. Judge "on-brand"
   against the actual industry's genre conventions (see checklist 7a) — do
   not dock this axis for content (e.g. skin in spa/wellness photography)
   that is normal and expected for the category; dock it for inconsistent
   treatment, low production value, or genuine mismatch with the brand's own
   stated tone.
6. **Responsiveness** — mobile is a designed layout, not a shrunken/stacked
   desktop; fold content prioritized for mobile; touch ergonomics respected.
7. **Motion & micro-detail** — transitions bounded (~150-300ms) and purposeful;
   hover/focus states exist; details (borders, shadows, radii) consistent;
   reduced-motion respected. Score 2 if simply absent-but-harmless.

## Few-shot critique examples (required specificity)

Good — names element, problem, and consequence:

> Typography 1: ヒーローの英字 "PRIVATE ONSEN SPA" が letter-spacing -0.05em で
> 詰まりすぎ、直下の日本語キャッチと行間も 1.2 で窮屈。高級感を出すべき
> 最上部が安売りバナーに見える。

> Spacing 2: 治療メニューカードは 8px グリッドに乗っているが、セクション見出し
> 「Menu」だけ左端がカード群より 4px 内側にズレており、並べて見ると事故に見える。

> Color 3: 全体は落ち着いた生成り+深緑で統一されているのに、予約CTAだけ
> 彩度の高い #FF6B35。目立つが世界観を壊しており、深緑の濃色+白抜きの方が
> ブランドに沿って目立たせられる。

Bad — rubric violations (do not write these):

> 全体的にきれいで読みやすい。 (names nothing)
> Color 3: 良い配色。 (no element, no reason)
> 修正済みのCSSは正しく8pxグリッドを使用。 (judging code, not pixels)

## Reporting

For each axis: `score — one-line justification`. Then pick the **single
highest-leverage improvement** overall and describe the fix concretely enough
that another agent could implement it without seeing this conversation.
