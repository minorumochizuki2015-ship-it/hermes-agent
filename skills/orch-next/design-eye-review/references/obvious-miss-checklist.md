# Obvious-Miss Checklist

Every item below is something a normal user notices within seconds, and that
agents have historically missed by reviewing code instead of pixels. Run each
item against the actual screenshots. `PASS` / `FAIL` / `N/A` + evidence for FAIL.

## A. Broken rendering (即死級 — any FAIL means the page looks broken)

1. **Text clipping/overflow** — no button, card, badge, or nav label cuts off
   text. Japanese labels (「ご予約はこちら」等) are the most common victims.
2. **Element overlap** — nothing sits on top of other content unintentionally
   (sticky header over headings, FAB over buttons, absolute-positioned art over text).
3. **Broken images** — no alt-text icons, 404 placeholders, stretched or
   squashed aspect ratios, or obviously wrong crops (heads cut off).
4. **Horizontal scroll on mobile** — the 375px full-page capture shows no
   sideways overflow (check for content wider than frame / cut off at right edge).
5. **Layout collapse at 375px** — multi-column sections stack cleanly; no
   single column squeezed to a sliver; no giant desktop-sized element.
6. **Unstyled flash / raw look** — no section renders as unstyled HTML,
   default-blue links, or Times New Roman fallback.

## B. Instantly-noticed quality problems

7a. **Photo content fit** — judge against genre norms for the actual
    industry, not against a generic, industry-blind notion of modesty. Spa,
    onsen, massage, and wellness photography conventionally includes skin,
    close treatment contact, and intimate proximity — that is standard,
    expected, and appropriate for premium brands in that category, not a red
    flag by itself. (Corrected 2026-07-10: a first pass on a real "private
    onsen spa" Menu hero — a close-up of skin during a massage — was flagged
    as brand risk. The client, judging as the actual target audience,
    confirmed it reads as appropriate and on-brand for the spa genre. Both
    the self-review and an independently dispatched audit made the same
    miscalibration — a shared blind spot, not a one-off, and a reminder that
    subjective/cultural fit judgments need the user's own eyes far more than
    other checklist items.) What to actually flag: photos that are
    inconsistent with the brand's *own stated tone* (e.g. a calm, therapeutic
    brand using a jarring or exploitative crop), that read as low
    production value or stock-photo generic, or that visibly contradict the
    service being sold — not skin exposure in a genre where it is normal.
    Always report this axis as a question for user judgment rather than a
    unilateral pass/fail; do not silently reject a photo the user hasn't
    weighed in on, and do not keep re-flagging one they've already accepted.
7b. **Competing text inside photos** — if the hero/background photo itself
    contains legible text (a sign, poster, product label), check whether it
    visually competes with the page's actual heading. Two similarly-weighted
    text blocks fighting for attention breaks the one-focal-point rule even
    when each element is individually well-designed.
7. **Low-contrast text** — no thin gray-on-white or low-opacity body text.
   Dark backgrounds: body text ≥ ~80% opacity, headings ≥ 90%. If you have to
   squint at the screenshot, users can't read it.
8. **Placeholder/mock content visible** — no lorem ipsum, 「仮」「サンプル」
   "TODO", default OG images, `example.com`, or an earlier rejected design
   still showing.
9. **First viewport fails to identify the product** — brand/service/place is
   visible and comprehensible without scrolling; hero is not decorative filler.
10. **Misalignment** — edges that should share a line do (section headings vs
    card grids, logo vs nav, icon vs label baselines).
11. **Inconsistent sibling spacing** — repeated items (cards, list rows, nav
    links) have visually equal gaps. Uneven gaps read as accidental.
12. **Inconsistent controls in one group** — same-role buttons/tabs share one
    height, radius, and selected style. No clickable-looking no-op state.
13. **Tap targets** — mobile touch targets ≥ ~44px; adjacent links not
    packed so tightly a thumb would mis-hit.
14. **Font fallback** — Japanese text renders in the intended face, not a
    default Mincho/system fallback or tofu (□). Compare heading vs body faces
    against the design intent.
15. **Japanese text setting** — body line-height comfortable (≈1.7-2.0 for JP
    body), no negative letter-spacing on JP text, headings don't break at
    unnatural points (e.g. 「温泉ス\nパ」), no viewport-scaled font soup.
16. **Fixed-element interference** — sticky headers/CTAs don't hide anchored
    content or the section a user just navigated to.
17. **Default artifacts** — page title is not "Astro"/"Vite App"/"localhost",
    favicon is not the framework default (visible in captures when applicable).
18. **Dead or accidental whitespace** — no half-empty band that reads as a
    loading failure; intentional whitespace is balanced, not lopsided.
19. **Scale sanity** — nothing comically oversized or ant-sized relative to
    the viewport (hero text, logos, icons); desktop capture doesn't look like
    a zoomed mobile layout.
20. **State honesty** — pending/empty/error areas visible in the capture look
    designed (spinner, skeleton, message), not blank or broken.

## Evidence format

```text
11. Inconsistent sibling spacing — FAIL — mobile__full.png — 店舗カード間の
    余白が 12px / 24px / 12px と不揃い。2枚目と3枚目の間だけ広い。
```
