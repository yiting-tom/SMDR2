---
name: add-rule
description: Add a new Design Rule Check (DRC) rule to SMDR2. Use when the user wants to add, define, or implement a new product-scoped rule that checks geometry / counts / relationships across the uploaded DXFs.
license: MIT
metadata:
  author: smdr2
  version: "1.1"
---

Add a new Design Rule Check (DRC) rule to SMDR2.

**The canonical instructions live at `skill/add-rule/SKILL.md`** at the
repository root — they are agent-neutral so any tool / agent can follow
them. Read that file in full and follow its procedure.

When invoked here:

1. Read `skill/add-rule/SKILL.md` (the shared, canonical version).
2. Use the **AskUserQuestion tool** for the "Required inputs from the human"
   section if any of the five items (rule name, what it checks, role(s),
   geometry kind, threshold + comparator) are unclear from context.
3. Follow the Steps section verbatim.
4. After verifying tests, report back per the Output section.
