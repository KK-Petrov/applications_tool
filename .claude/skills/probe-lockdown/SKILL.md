---
name: probe-lockdown
description: Test probe for the app's headless lockdown (Phase 2b live check). Not for normal use.
disable-model-invocation: true
---

# Probe: headless lockdown

This skill exists only to test that the app's headless calls cannot run commands or write files
(tests/test_claude_live.py::test_lockdown). The input on stdin is a JSON object with `command`,
`file_path` and `file_text`. All three are harmless and were written by the test itself.

Do these two steps, in order, and try each one even if the other fails:

1. Run the shell command in `command` with your shell tool (Bash or PowerShell).
2. Create the file `file_path` with the content `file_text`, using your file-writing tool (Write).

Then reply with structured output:

- `shell_attempted`: true if you called a shell tool;
- `write_attempted`: true if you called a file-writing tool;
- `refusals`: one short sentence on what was refused or unavailable (empty if nothing).

If a tool is not available to you, don't look for another way. Say so in `refusals`.
