# LLMO/AIO (AI検索最適化) — technical implementation, contested effectiveness

Read this when a brief includes search/AI-answer visibility, not for a
purely visual Pass-1 plan — this is a separate concern from
`reference-site-teardowns.md`'s design-craft catalog, split out so a
visual-only task doesn't need to load it.

Two full research passes (2026-08-02) found genuine, dated, numeric
evidence on multiple sides of "does this actually work," not a settled
consensus. The rule this file follows: **every claim carries its own
source next to it** (not a shared preamble covering several claims at
once), and the status table below is authoritative — if a bullet's status
looks more confident than its table row, the table wins.

## Status at a glance

| Claim | Status | Strongest evidence |
|---|---|---|
| Classic SEO/authority-building (referring domains) predicts AI citation | **CONFIRMED** | SE Ranking 129k-domain ML study: strongest measured predictor |
| Front-loading the direct answer increases AI citation | **CONFIRMED, narrow** | Ahrefs: 44.2% of citations draw from a page's first 30% |
| Front-loading applied site-wide is safe for SEO too | **DEBUNKED** | Zenn/kenimo49 first-hand test: raised AI citations, measurably cut Google organic traffic |
| `FAQPage`/`Article`/`HowTo` schema markup improves AI citation | **DEBUNKED** | Ahrefs controlled testing: ~zero measurable uplift; Google's own guide: not needed |
| `llms.txt` improves AI citation or traffic | **DEBUNKED** | Fernández 10-site test: no attributable gain; SE Ranking: negative predictive value; Google's Mueller: "no AI system currently uses llms.txt" |
| `llms.txt` is harmless, low-cost infrastructure worth having anyway | **CONTESTED, leans yes** | 8.8× adoption growth despite 97% zero-request rate — practitioners keep adding it as a hedge, not for measured ROI |
| `Content-Signal` robots.txt directive changes crawler behavior today | **DEBUNKED (for now)** | Google's Mueller: "no effects whatsoever for any crawler or LLM" |
| Ranking well on Google predicts AI-answer citation | **CONTESTED, weakening** | Ahrefs: top-10/AI-citation overlap fell 76%→38% in under a year; 28.3% of top ChatGPT citations rank nowhere on Google |
| LLMO replaces classic SEO | **DEBUNKED** | Google's own guide: AI optimization "is still SEO"; every large study above treats them as additive, not substitutable |
| Ongoing multi-LLM citation measurement (not one-time tactics) is worth doing | **CONTESTED, industry-insider view** | Principle's own "LLMO不要論は本当か" piece — read with its commercial-interest caveat below |

## Terminology (don't use these as interchangeable buzzwords)

- **SEO** — rank in the search-results page. Still foundational;
  insufficient alone.
- **AIO (AI Optimization)** — the umbrella goal: get your information
  surfaced correctly and favorably inside AI search/answer products at all.
- **LLMO (Large Language Model Optimization)** — the specific mechanics of
  being *cited*. Often described as citation-probability replacing
  backlinks as the currency — see the status table: the largest study
  found referring-domain count is still the strongest predictor, so treat
  "LLMO replaces link-building" as marketing framing, not a confirmed
  mechanism.
- **GEO (Generative Engine Optimization)** — the most aggressive goal: your
  content becomes part of the AI's generated answer *text itself*. Also
  the term's real origin: a KDD 2024 paper, not a marketing coinage
  (below).

## The origin: a real academic paper, and what it actually tested

**"GEO: Generative Engine Optimization"** — Aggarwal, Murahari, Rajpurohit,
Kalyan, Narasimhan, Deshpande (Princeton/IIT Delhi/Georgia Tech/Allen
Institute for AI). [arXiv:2311.09735](https://arxiv.org/abs/2311.09735),
submitted 2023-11-16, published at ACM SIGKDD (KDD) 2024. Built a benchmark
(GEO-bench) and tested content-level interventions, measured by "how much
source text appears in the generated answer, weighted by citation
position." Relative improvements found: quotation addition +27.8%, adding
statistics +25.9%, citing sources +24.9%, authoritative tone +21.8%, unique
words +20.7%, fluency optimization +25.1%. **Keyword stuffing — the one
classic-SEO tactic tested — underperformed the baseline.** Combined best
techniques: up to +40% visibility, with explicit domain-dependent variance.
**Caveat**: this is the origin of the widely-repeated "40%" statistic, but
it's ~2.5–3 years old and predates today's live-RAG ChatGPT
Search/AI Overviews/AI Mode — cite it as foundational, not as proof of
current-system behavior.

## What large-scale 2026 observational data actually shows

Each bullet names its own source — don't assume one preamble covers all of
them, several different studies are cited in this section:

- **Rank-citation decoupling is accelerating** — pages cited in Google AI
  Overviews that also ranked top-10 organically fell from 76% (mid-2025)
  to **38%** (early 2026); 28.3% of ChatGPT's most-cited pages have
  **zero** Google organic visibility. Source: [Ahrefs, "1 billion data
  points across 14
  studies"](https://ahrefs.com/blog/ai-search-traffic-conversions-ahrefs/)
  (~June 2026; not every individual sub-statistic in this bullet had one
  fetchable primary URL at research time — this is the best available
  primary link for the study as a whole).
- **Format and position matter**: "Best X"-style listicles dominate
  ChatGPT citation types (43.8% per one cut of the data); headlines that
  directly answer the query get cited 41% of the time vs. 29% for
  loosely-related headlines; 44.2% of citations draw from the **first
  30%** of a page. Source: same Ahrefs study above. **Read this together
  with the DEBUNKED row above**: front-loading correlates with being
  cited *more*, but a controlled first-hand test (below) found applying it
  as a blanket site-wide rule can cost you Google organic traffic at the
  same time — treat it as a tactic for citation-oriented page types
  specifically, verified per-page, not a universal rewrite instruction.
- **Structured data (schema.org) showed ~zero measurable citation uplift**
  in Ahrefs' controlled testing, and content length has ~zero correlation
  with citation (r=0.04) — same study. This directly **contradicts**
  vendor-consensus advice that treats `FAQPage`/`Article`/`HowTo` schema as
  a priority citation tactic; the harder data says it isn't one, at least
  not yet, at least not for citation specifically (it may still be worth
  having for other reasons — general SEO hygiene, rich-result eligibility —
  just not *this* reason).
- **67% of ChatGPT's top citations come from sources marketers cannot
  influence** (Wikipedia, brand homepages, app stores) — same Ahrefs
  study — a sobering realism check on how much of this is actually
  actionable.
- **Referring-domain count remains the single strongest measured
  predictor** (350k+ referring domains → 8.4 avg. citations vs. 1.6–1.8 for
  <2,500 — classic authority-building still dominates). Source: [SE
  Ranking, 129,000-domain/216,524-page ML
  study](https://seranking.com/blog/how-to-optimize-for-chatgpt/),
  corroborated by [Search Engine
  Journal](https://www.searchenginejournal.com/new-data-top-factors-influencing-chatgpt-citations/561954/).
  Same study: YouTube mentions correlate strongly (r=0.737) — flagged
  explicitly as correlation, not demonstrated causation. **Same study's
  own explicit llms.txt finding**: "LLMs.txt files showed negligible
  impact... removing it improved predictive results" — their data science
  team found it actively unhelpful as a model feature, and their own
  advice is to skip it and invest in earning mentions on Reddit/Quora
  instead (~4× citation correlation in their data).
- **llms.txt adoption and actual consumption are moving in opposite
  directions**: [Originality.ai](https://ppc.land/llms-txt-adoption-rises-8-8x-but-97-of-files-get-zero-ai-requests/)
  tracked 3M+ sites over 12 months and found llms.txt adoption grew
  **8.8×** (4,088→36,120 sites) in the same window Ahrefs' 137,000-domain
  server-log analysis found **97% of llms.txt files receive zero
  AI-crawler requests**.

## Official platform statements, tiered by actual evidentiary weight

Only Google has published genuinely detailed, on-the-record guidance; the
rest confirm mechanics, not source-selection logic.

- **Google — high confidence, directly quoted.**
  [developers.google.com/.../ai-optimization-guide](https://developers.google.com/search/docs/fundamentals/ai-optimization-guide)
  (last updated 2026-07-10): *"optimizing for generative AI search is
  optimizing for the search experience, and thus still SEO."* Explicitly
  says **not needed**: new machine-readable/"AI text" files (i.e. llms.txt,
  by name), chunking content for AI, an AI-specific writing style, or
  special schema.org markup. Google's own spokesperson **John Mueller**
  went further on this exact point: *"no AI system currently uses
  llms.txt... it's super-obvious if you look at your server logs"*
  ([source](https://bsky.app/profile/johnmu.com/post/3lrshm4gggs2v), Jun
  2025). Scope limit: this covers only Google's own AI Overviews/AI Mode,
  not ChatGPT/Claude/Perplexity.
- **OpenAI — medium confidence, mechanism only.** [Web search tool
  docs](https://developers.openai.com/api/docs/guides/tools-web-search)
  document the `url_citation` annotation object and a `sources` field
  ("the complete list of URLs the model consulted") but publish no
  ranking/selection criteria.
- **Anthropic — medium confidence, mechanism only.** [Web search tool
  docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)
  document that Claude's web search runs on Brave Search as backend
  provider and auto-cites in responses, again with no published
  ranking/selection criteria.
- **Perplexity — medium confidence on crawl policy, none on ranking.**
  [PerplexityBot
  docs](https://docs.perplexity.ai/docs/resources/perplexity-crawlers)
  confirm it honors `robots.txt`; Perplexity runs a named revenue-sharing
  Publishers Program. No official citation-ranking methodology published —
  every "how Perplexity ranks sources" article in circulation is
  third-party reverse-engineering, not documentation.

**Bottom line**: most circulating "how to get cited by ChatGPT" advice —
including a real share of what LLMO/GEO agencies sell — is inference from
observational studies or reverse-engineering, not confirmed platform
documentation. State only what traces to an actual platform statement or a
named study's methodology as fact; flag everything else as "commonly
claimed, not confirmed" (see status table).

## Real practitioner evidence, both directions

- **10-site before/after test** (Ana Fernández, [Search Engine
  Land](https://searchengineland.com/does-llms-txt-matter-467740), Jan
  2026): only 2/10 sites saw AI-traffic gains after adding llms.txt, and
  neither gain was attributable to the file itself (PR and content changes
  landed in the same window); 8/10 no change; 1/10 fell -19.7%. Conclusion:
  llms.txt is "useful infrastructure, not a growth lever," like a sitemap.
- **1,500-site AI-readability audit** ([Website AI
  Score](https://websiteaiscore.com/blog/case-study-1500-websites-ai-readability-audit)):
  30% of sites **accidentally** block AI bots via stale robots.txt/security
  defaults; only **3/1,500 (0.2%)** had a valid llms.txt; 70% had zero
  schema markup; 40% are so JS-dependent they render as empty shells to
  non-hydrating AI crawlers. A live, almost funny confirmation of the whole
  field's premise: Perplexity was already citing this very audit's "0.2%"
  statistic within 3 hours of the post going up (via the [Hacker News
  thread](https://news.ycombinator.com/item?id=46632157) discussing it).
- **A genuine SEO-vs-LLMO trade-off, first-hand, Japanese**
  ([Zenn.dev, kenimo49](https://zenn.dev/kenimo49/articles/llmo-seo-tradeoff-coexist-design)):
  applying blanket LLMO tactics (front-loaded answers, trimmed preambles,
  question-style headers) site-wide raised AI-engine citations but
  **measurably dropped Google organic traffic** in Search Console —
  front-loading hurt dwell time, over-trimming read as thin content to
  Google. Proposes tiering tactics by page role and tracking SEO/LLMO as
  separate KPIs rather than applying one playbook everywhere. The single
  most actionable, non-hyped finding in this whole research pass — this is
  the source for the "DEBUNKED, narrow" row in the status table above.
- **The HN mega-thread** ([2024, 206
  points](https://news.ycombinator.com/item?id=41439983)) is worth reading
  once for calibration: skepticism that anyone should volunteer to make
  scraping easier, a "theft dressed up as business as usual" objection, and
  — notably — the llms.txt spec's own author (**jph00**, Jeremy Howard)
  directly clarifying in the thread that the proposal "isn't really for
  training, it's for end users who want to know what information to
  include when they're using models" — a narrower, less commercially
  loaded original intent than most vendor pitches built on top of it.
- **Japanese LLMO content-marketing pattern, flagged as a negative
  example**: a checked post at `note.com/stock_value0407` (a GEO/LLMO
  consulting operation) claims "AI citation rate up to 70%" from a client
  engagement while disclosing no client name, industry, timeframe, or
  methodology — while promoting the same firm's paid diagnostic tool in
  the same post. Representative of a real volume of unverifiable "case
  study" content in this space; treat any LLMO claim without a disclosed
  methodology as marketing, not evidence — the same standard applied to
  every other claim in this file.

## The Content-Signal directive: real, deployed, currently inert

[Cloudflare](https://blog.cloudflare.com/content-signals-policy/) (Will
Allen, Sep 2025) proposed the `Content-Signal` robots.txt extension
(`search`/`ai-input`/`ai-train`, each yes/no/unset), partly as an EU
Copyright Directive 2019/790 Art. 4 (TDM opt-out) legal reservation, not
purely a technical control. Rolled out by default to 3.8M+
Cloudflare-managed domains; recognized by Lighthouse as of v13.0.2 (merged
[Chromium PR](https://github.com/GoogleChrome/lighthouse/pull/16767), Jan
2026); an open, unimplemented proposal exists to add it natively to Next.js's
`MetadataRoute.Robots` API ([vercel/next.js Discussion
#85382](https://github.com/vercel/next.js/discussions/85382), still open
as of Oct 2025). **But**: Google's John Mueller stated it has "no effects
whatsoever for any crawler or LLM" today ([Search Engine
Roundtable](https://www.seroundtable.com/google-cloudflare-content-signals-41631.html)).
quoitworks.com uses it (below) — a real, principled, forward-looking
legal/policy stance worth having for the reason it exists (an explicit,
citable opt-out position), just not, currently, a technical lever that
changes crawler behavior. Say both things; don't oversell it as
functional today.

## What real sites actually do, measured directly (`curl`)

Checked both the three sites in `reference-site-teardowns.md` and
companies that explicitly sell LLMO/AIO as a service. **Note throughout**:
what's being verified here is *implementation completeness* — does the
technical artifact exist and is it well-structured — not *demonstrated
citation or traffic effectiveness*, which per the sections above remains
largely unproven for llms.txt/Content-Signal specifically. A
well-structured llms.txt is still a bet, not a lever with confirmed ROI.

- **quoitworks.com** — the most complete implementation found across all
  sites checked in this research, notable because it's also the agency
  literally selling "SEO・LLMO/AIO対策" as a service — a rare case of a
  vendor's own site technically matching its pitch. `robots.txt` carries
  the `Content-Signal` directive above. Its `/llms.txt` (200) is worth
  reading in full as a structural template — not because it's proven to
  work, but because if you're going to spend the (low) cost of having one,
  this is what "well-structured" looks like: a one-paragraph company
  summary; explicit name disambiguation (human-facing vs. formal vs.
  English brand name, so an LLM doesn't garble them); a plain company-facts
  block; a full service/page directory with real URLs; a **related-entity
  hierarchy** section naming adjacent brands (a design-gallery site, the
  representative's separate public persona, a video channel) and
  explicitly stating they should *support*, not replace, the main entity;
  a **proactive misclassification correction** ("should not be summarized
  only as an SEO agency, AI tool company, low-cost template vendor, or
  individual creator brand"); and an explicit **hallucination-mitigation
  instruction** telling AI systems not to infer guaranteed results from
  individual case studies.
- **GreenBanana SEO** (Boston, US;
  [service page](https://greenbananaseo.com/services/generative-engine-optimization-agency/)) —
  a second real match between marketing and technical execution.
  `robots.txt` explicitly allowlists `GPTBot, ChatGPT-User, ClaudeBot,
  Claude-Web, PerplexityBot, Google-Extended, XAI-Crawler, CCBot,
  OAI-SearchBot, Applebot` with `Allow: /`; `/llms.txt` (200, 7.4KB) is
  genuinely hand-authored with citation/attribution guidance (canonical
  host preference, founder attribution, an explicit "don't reproduce full
  articles verbatim" instruction) — less elaborate than quoitworks'
  version (no entity-disambiguation or hallucination-correction sections)
  but real, not boilerplate.
- **Vendors whose own site contradicts their marketing — a useful
  diagnostic heuristic**: [株式会社メディアリーチ (Media
  Reach)](https://mediareach.co.jp/blog/llmo-company) sells "LLMO診断" and
  claims "AI引用率420%向上" for clients, but their own `robots.txt` is a
  bare 3 lines with no AI-crawler treatment and `/llms.txt` → 404.
  [Netpeak Agency USA](https://netpeak.us/services/generative-engine-optimization/)'s
  `/llms.txt` exists but reads as generic auto-generated company
  boilerplate, not a citation-control document, and their `robots.txt`
  has zero AI-specific rules. [株式会社プリンシプル
  (Principle)](https://www.principle-c.com/service/llmo/)'s `/llms.txt`
  (200, ~12KB) turned out to be **Yoast SEO's unmodified default output**
  — a plain categorized link dump, the low-effort end of the spectrum.
  **When evaluating any AIO/LLMO vendor's credibility, checking whether
  their own `robots.txt`/`llms.txt` matches their claims is a fast,
  concrete, five-minute gut-check** — in this sample of 5 vendors checked
  (quoitworks, GreenBanana, Media Reach, Netpeak, Principle), 2 clearly
  passed, 3 did not.
- **The skeptical view, from inside the industry itself**: 株式会社プリンシプル
  (the same firm behind the generic Yoast llms.txt above) separately
  published [a piece questioning whether LLMO is
  necessary](https://www.principle-c.com/column/google_io_2026_is_the_llmo_is_dead_theory_true/)
  in light of Google's official guide. Its actual argument, read in full
  rather than judged by the headline: it largely *agrees* hacky tactics
  (llms.txt-chasing, content-chunking, AI-specific writing style) are
  dead, but argues Google's guide only covers Google's own AI
  features — ChatGPT/Claude/Perplexity run separate, opaque logic, which
  the platform-statements section above confirms is true. Its actual
  conclusion: LLMO-as-tricks is dead; LLMO-as-**ongoing multi-LLM
  measurement and verification**, built on solid SEO fundamentals rather
  than replacing them, remains legitimate. Necessary caveat, stated
  plainly: this conclusion also happens to justify buying the redefined,
  ongoing paid service from firms positioned exactly like the one writing
  it — coherent on its own logic, but this is an informed industry-insider
  take with a commercial stake, not neutral evidence. Weigh it below the
  harder Ahrefs/SE-Ranking data above, not alongside it as equal-weight
  evidence.

## Open-source tooling landscape: real but bimodal

A few substantial, actively-maintained projects surrounded by a long tail
of thin, single-author, often-abandoned ones — say so plainly rather than
inflating the thin categories:

- **llms.txt spec** — [AnswerDotAI/llms-txt](https://github.com/AnswerDotAI/llms-txt)
  (2,540★, authored by Jeremy Howard/Answer.AI, Sep 2024, still active).
  Adoption reached mainstream SEO tooling: Yoast SEO ships a native
  generator ([spec](https://developer.yoast.com/features/llms-txt/functional-specification/)).
- **AI-crawler blocking** —
  [ai-robots-txt/ai.robots.txt](https://github.com/ai-robots-txt/ai.robots.txt)
  (4,025★, the flagship, actively updated) community-maintains a
  `robots.json` that auto-generates robots.txt/.htaccess/nginx/Caddy
  configs. Notable: general-purpose bot detectors that organically added
  AI-bot patterns —
  [omrilotan/isbot](https://github.com/omrilotan/isbot) (1,154★ but
  ~24.9M npm downloads/**week**) — dwarf every AI-specific tool in
  real-world usage.
- **GEO-score/citation trackers** —
  [Auriti-Labs/geo-optimizer-skill](https://github.com/Auriti-Labs/geo-optimizer-skill)
  (634★, CLI/Python/MCP/Astro, audits robots.txt access for 27 AI bots +
  llms.txt + JSON-LD + content quality into a weighted 0–100 score) is the
  most complete tool found. Worth a specific mention:
  [Pupok462/open-geo](https://github.com/Pupok462/open-geo) (17★) is built
  **as a Claude Code skill** using the Claude-in-Chrome extension to read
  real rendered AI answers across 7 engines without paid API keys — a
  direct structural peer to the research method used to build this file.
- **AI-readiness schema (JSON-LD) generators — the weakest category**: no
  substantial, actively-maintained standalone project exists; what
  capability exists is folded into the broader GEO-audit toolkits above,
  not sold as its own thing. Most "AI-ready schema generator" search
  results are commercial SaaS with no public repo at all. State this
  plainly rather than padding it with thin matches.

## The general, durable lesson for any project's SEO surface

Whatever a project decides about the contested llms.txt/Content-Signal
questions above, a working `robots.txt` and `sitemap.xml` are **the one
part of this whole research area with no serious controversy attached** —
every source above, pro- or anti-LLMO, treats them as basic infrastructure
that should exist and be correct. If a project's framework can generate
both from one small config file (as most modern frameworks can), there's
no real argument for skipping it, independent of whatever a team decides
about the more contested tactics in the status table above.
