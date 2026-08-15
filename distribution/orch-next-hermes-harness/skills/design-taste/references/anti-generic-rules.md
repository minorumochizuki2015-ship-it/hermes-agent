# Anti-Generic Rules — deterministic pre-code lint

Run these as yes/no checks on the Pass 1 design plan and again on the
implemented page. Each "yes" means the generated look is leaking in.

1. Is the palette cream/terracotta+serif, or near-black+neon accent, chosen
   without a subject-grounded reason?
2. Are Inter/Roboto/Arial/system-ui the *only* faces? (Utility use is fine;
   sole-face-as-design is the failure.)
3. Do 01/02/03 markers number something that is not actually a sequence?
4. Is the hero an abstract gradient/SVG blob while the business is a real
   place, product, or person the visitor wants to see?
5. Are there cards inside cards, or a card grid wrapping non-repeated content?
6. Does every section fade/slide in on scroll?
7. Could the hero headline be pasted onto a competitor's site unchanged?
   (「最高の体験を、あなたに。」-class copy)
8. Are there more than 2 border-radius values, more than 2 shadow styles, or
   more than 6 colors doing the work of 4?
9. Is the section rhythm identical stacked bands of equal height with
   centered heading + 3 columns, repeated 4+ times?
10. Is any animation longer than ~300ms for a routine transition, or is motion
    required to understand where things went?
11. Does dark-mode/dark-surface text sit below ~80% opacity for body copy?
12. Is the same 64px/96px vertical padding applied between every section
    regardless of content relationship?

Items 1-7 are direction-level: fix in the design plan.
Items 8-12 are execution-level: fix in code review before screenshots.
