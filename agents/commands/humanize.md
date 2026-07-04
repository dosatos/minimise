---
description: Improve a document for human readability — cut noise, keep all content
---

Review the following document and improve it for human readability. Do not add any new information, ideas, or requirements that are not already present. Your goal is to reduce noise while preserving all core content.

Apply the following edits:

1. **Cut verbosity** — Remove filler phrases, redundant preambles, and over-explained obvious points. Say things once, clearly.
2. **Remove redundancies** — If the same idea, constraint, or logic appears in multiple places, consolidate it into the most appropriate location and remove the duplicates.
3. **Fix ordering** — Ensure information is presented in the order a human reviewer needs it: context before detail, decisions before rationale, what before how.
4. **Highlight what matters** — Surface key decisions, assumptions, risks, and open questions so they are immediately visible, not buried in prose.
5. **Tighten structure** — Remove sections or headers that add no value. Merge thin sections. Break up dense walls of text only if it genuinely aids scanning.
6. **Close logic gaps** — If a section references something that was never defined, or a conclusion doesn't follow from what preceded it, flag it with a comment like [GAP: briefly describe the issue] rather than inventing a fix.

Rules:
- Do not invent content. If something is unclear, flag it with [UNCLEAR: ...] rather than guessing.
- Do not change the meaning of any statement.
- Do not remove information simply because it is detailed — only remove it if it is repetitive or adds no value.
- Preserve all decisions, constraints, open questions, and named entities exactly as written.
- The output should be the revised document only, with inline [GAP] or [UNCLEAR] flags where needed.

Document to humanize: $ARGUMENTS
