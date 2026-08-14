# Six-block image-gen dispatch template

Fill every block for a generated UI asset candidate task.

```text
[1] ROLE AND TASK
Generation-only for <asset purpose>. No production UI/code changes.
Selected visual contract: <contract id/digest and direction>.

[2] READING OBLIGATIONS
1. <approved reference>
2. <prior accepted visual family, if any>
3. <human UX route and asset-needs decision>

Before generation answer: what must be preserved, and what may differ?

[3] ENVIRONMENT FACTS
- Target dimensions/aspect ratio:
- Palette/style:
- Transparency/crop constraints:
- MUST NOT include:
- Prior rejection reasons:

[4] STRICT SCOPE
Write only to isolated staging: <path>
Forbidden: production assets, UI source, tests, integration, commit, push,
deploy, credentials, or unapproved provider use.

[5] SPEC
- Purpose and composition:
- MUST appear:
- MUST NOT appear:
- Produce exactly 2-4 comparable candidates:
- Comparison axes:

[6] ACCEPTANCE AND HONEST REPORTING
- Open every candidate and report actual measured dimensions.
- Verify every MUST and MUST NOT item for every candidate.
- Verify palette/style and transparency/crop constraints.
- Disclose tool limitations and failed candidates.
- Do not select or integrate on the human's behalf.
```

Parallel candidate tasks require separate worktrees or non-overlapping staging
directories. They are mutually exclusive at integration: no loser merge and no
production write until technical review and direct human selection.
