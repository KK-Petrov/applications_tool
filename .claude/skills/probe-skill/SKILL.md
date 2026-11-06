---
name: probe-skill
description: Test probe for the app's Claude Code wrapper (Phase 2b live check). Not for normal use.
disable-model-invocation: true
---

# Probe: skill expansion

This skill exists only to prove that a project skill named at the start of a `claude -p` prompt is
expanded (tests/test_claude_live.py::test_skill_expansion).

Ignore the input on stdin. Reply with structured output exactly:

```json
{"token": "probe-46bd34beb7613259"}
```

The token appears only in this file, so returning it proves this file was read.
