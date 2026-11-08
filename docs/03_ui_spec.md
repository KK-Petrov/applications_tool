# 03: UI specification

The agreed mockup lives on the user's private design canvas; this spec is the complete written version.
Where the mockup and this spec differ, this spec wins.

## 1. Architecture and routes
FastAPI serves Jinja2 templates; HTMX swaps partials for in-place edits (status cells, checkboxes,
detail panel). Every HTMX endpoint also works as a normal form POST that redirects back (no-JS fallback).
App binds to `127.0.0.1:8765` only.

| Route | Page / partial |
|---|---|
| `GET /` | Dashboard |
| `GET /programmes/{id}` | Programme page |
| `GET /programmes/new`, `POST /programmes` | Add programme (manual form; Research button added in Phase 2c) |
| `GET /programmes/{id}/edit`, `POST /programmes/{id}` | Edit programme |
| `POST /programmes/{id}/verify` | Mark verified (docs/02 §6) |
| `POST /programmes/{id}/requirements/{rid}`, `/steps/{n}`, `/documents/{did}` | Toggle / set status (partial) |
| `POST /programmes/{id}/interviews` | Add or edit an interview/assessment |
| `POST /programmes/research`, `POST /programmes/research/save` | Research a new programme (2c): runs the skill and shows the domains step; with `step=check` checks the confirmed domains and shows the preview (editable deadline rows); save re-runs every check and creates the file. Entry form on the dashboard and Add programme |
| `POST /programmes/{id}/research`, `POST /programmes/{id}/research/apply` | Re-research (2c): runs the skill, then the domains step (programme without `official_domains`) or the comparison; `step=check` redoes the checks without a new call; apply saves the accepted rows (`accept`, with the comparison's file `digest`); `POST /programmes/{id}/research/select` is Accept all changes / Clear all without JS (`select=all|none`): the comparison again with those rows ticked, no call, nothing written, the comparison's `digest` carried through (DECISIONS #68) |
| `GET /recommenders` | Recommender sheet (query: `q`, `filter`, `sort`, `selected`) |
| `GET /recommenders/new`, `POST /recommenders` | Add professor |
| `GET /recommenders/{id}/panel` | Detail panel partial |
| `POST /recommenders/{id}/letters/{programme_id}` | Set letter status (partial: the cell + the row's next action) |
| `POST /recommenders/{id}/email` | Generate email (Phase 2b): HTMX gets the panel, with the row and totals out of band; no-JS redirects to the panel; a failure shows its message in the panel and writes nothing |
| `GET /recommenders/course-suggestions?name=` | Add professor: courses whose lecturers match the name (partial, 2c) |
| `GET /courses` | Courses list (minimal, 2c; shown in full on the biography bank page in 4a) |
| `GET /courses/import`, `POST /courses/import`, `POST /courses/import/send`, `POST /courses/import/save` | Import courses: upload or paste → review of the stripped text (no call) → Send to Claude → preview, accept per row or all (2c; DECISIONS #70) |
| `POST /courses/import/professors` | Create professors from the document: preview, then batch create (2c) |
| `GET /essays`, `GET /essays/{programme_id}/{essay_id}`, `POST …/versions`, `POST …/evaluate` | Essays (4a/4b) |
| `GET /cv`, `GET /cv/{base or programme_id}`, `POST …/versions`, `POST …/evaluate` | CVs (4a/4b) |
| `GET /biography`, `POST /biography/entries`, `POST /biography/entries/{id}` | Biography bank (4a) |
| `GET /usage` | AI usage (read-only; query `month=YYYY-MM\|all`, `source=app\|eval\|live\|unknown\|all`; DECISIONS #60) |
| `GET /settings`, `POST /settings` | Owner details, display zone, Google connection, Sync now (3) |
| `POST /display-tz` | Header zone selector |

## 2. User stories (each is an end-to-end Playwright test)
- **US-1** Dashboard lists all programmes sorted by deadline with days left, letters, documents, status and data badge.
- **US-2** Switching the header zone to Rotterdam changes every displayed time; the choice persists after reload.
- **US-3** On a programme page the user ticks a requirement and a step and sets a document to "sent"; reload shows them saved.
- **US-4** The user adds a programme by form; it appears on the dashboard marked unverified; editing it works.
- **US-5** The user marks a programme verified and adds an interview with date, time and zone; both persist.
- **US-6** The user adds a professor with a pasted 3,000-character course description and two programmes; the row appears with "To ask"/"—" cells correct and the description is stored verbatim.
- **US-7** Clicking a status cell and choosing "Requested" updates the cell, stamps today's date and updates the row's next action.
- **US-8** Search, filter (Needs action / Waiting on professor / Done) and sort (next action date, name, last contact) work together.
- **US-9** Clicking a row opens the detail panel; editing the course saves; the email address is not visible in the sheet.
- **US-10** (2b) "Generate email" shows a subject and body; Copy copies it; "Mark as requested" sets the selected letters to requested.
- **US-11** (4a) The user adds, edits and deletes biography entries, including an anticipated one.
- **US-12** (4a) The user pastes an essay draft; the word count and wrong-school hits show; saving creates v1, then v2.
- **US-13** (4a) The user compares v2 with v1 in a side-by-side diff.
- **US-14** (4a) The user saves a base CV and a tailored CV for one programme.
- **US-15** (4b) "Evaluate" shows the four rubric ratings with reasons and comments anchored to paragraphs.
- **US-16** (2c) "Research a programme" fills a new programme marked unverified with official sources.
- **US-17** (3) Settings → "Sync now" reports created/updated/deleted counts (fake Google API in tests).
- **US-18** (2c) On a programme page, "Re-research" shows each field as new / changed / unchanged with its source;
  the user accepts or rejects each change; nothing is written until Save; accepted deadline changes reset those
  deadlines to unverified.

- **US-19** (2c) The user imports a transcript (.docx, text PDF or pasted text); the preview shows every course with all fields, flags grades not found in the document, and writes nothing until rows are accepted; a scanned PDF is refused with a clear message.
- **US-20** (2c) After the import, "Create professors from this document" lists every lecturer once with their courses and grades; the user ticks some, links one to an existing professor, and creates the rest in one batch; the new professors show "Email missing".
- **US-21** (2c) On Add professor, typing a lecturer's name suggests their courses; choosing one fills course, description, grade and term, which stay editable.

## 3. Layout and navigation
- Left sidebar (dark, 220 px min, stacks on top below 760 px): brand "Application Hub"; links Dashboard,
  Programmes, Recommenders, Essays and CV, Biography bank, AI usage, Settings; footer: "Reminders sync to Google
  Calendar “Applications” · Daily digest at first login" plus last sync time.
- Main column: max width 1120 px (1240 px on the recommender sheet), padding 32 px 40 px.
- Header row on every page: date ("Friday 30 October 2026"), zone selector ("Times in: Eastern Time
  (Charlottesville) / Central European Time (Rotterdam)"), page title, primary action buttons right.

## 4. Pages
### 4.1 Dashboard — main message: "what is due next, and what is missing"
- Alert strip (orange) for the single most urgent action from docs/02 §4–5 (e.g. "Request Princeton
  recommendations by 3 November … 3 letters needed, 0 confirmed").
- Four stat tiles: Next deadline (days, programme, date), Programmes tracked (+ unverified count),
  Letters confirmed (of total required), Essays evaluated (of total required).
- Programmes table: Programme (name + unit) · Deadline (date + time + zone) · Days left · Letters ·
  Documents · Status · Data badge (with reason). Horizontal scroll box on small screens.
- Upcoming reminders: the next 8 reminders (date, text, category chip) from the reminder engine.
### 4.2 Programme page — main message: "everything this application needs, and where it stands"
Breadcrumb; title; subtitle (unit · length · how to apply) directly under the title; the actions in their own row below it (DECISIONS #63); chips: deadline with days, converted time,
data badge, status. Buttons: Edit, Mark verified, Re-research (2c). Source box (dashed): source links with checked date and
the verification note. Cards: Requirements (checkboxes, count), Application steps (numbered, ticks),
Documents and score reports (table: item, how sent, status menu, updated), Interviews and assessments
(rows with date/zone, estimated flag, status, notes; "Add interview or assessment"), Curriculum
highlights, Linked items (recommenders x/required, essays evaluated, AI-use policy, reminders count).
### 4.3 Recommender sheet — main message: "who has been asked, for what, and what to do next"
- Totals strip: per programme "confirmed / required · deadline".
- Toolbar: search (name, course), filter select, sort select, count "N professors", Import CSV (columns:
  name, email, university, course, course_description; optional grade, term, interaction), Add professor.
- Table: Professor · Course · Grade · one column per programme (status cell) · Last contact · Next action.
  Sticky header; selected row highlighted. Status cell = a button showing the status chip and a caret;
  clicking opens a menu with all statuses; choosing one posts and swaps the cell and the row.
  Status colours: no letter (not assigned) "—" grey text; To ask (`not_asked`) grey chip; Draft ready blue
  (set only by email generation, 2b); Requested orange; Confirmed green tint; Submitted solid green;
  Declined red tint. Legend under the table. (DECISIONS #39)
  A professor without an email shows an "Email missing" chip in the Professor cell and in the panel;
  email generation still works. The Course column shows the linked courses' names (DECISIONS #44).
- Detail panel below the table for the selected row: details (email shown here only), Edit, Generate email,
  latest generated email with Copy, "Refine in Claude Code" (copies a ready command to the clipboard:
  `claude "/rec-email <professor_id>"`), Mark as requested.
### 4.4 Add professor — form fields in docs/01 §4: Name, Email ("Used to address the email; not shown in
the sheet"), University select, Course, Course description (large textarea, ≥ 8 rows, hint "Paste it straight
from the course guide or syllabus"), Grade, Term, Specific interaction, programme checkboxes with deadlines,
Tone (Formal default / Slightly warmer). Buttons: Save, Save and generate email (2b), Cancel (Save first
so that Enter never starts a generation; DECISIONS #51). Title (default "Professor") from Phase 2b.
Phase 2c (DECISIONS #44): Title (default "Professor", pre-filled from a title in the name); Email optional;
linked courses (chips, removable). Typing a name shows suggested courses whose lecturers match the surname;
choosing one links it and fills course, description, grade and term; everything stays editable and
nothing auto-saves. Suggestions need JS (HTMX as you type; "Use …" fills the fields in the browser); without JS
the fields are typed by hand. Linked courses are ticked checkboxes (untick to unlink). A blank Title is taken from a title
token in the name, else Professor; with JS an untouched Title follows the name (DECISIONS #64).
### 4.5 Essays and CV — left list grouped by programme then "CV"; centre: prompt, version chip, live word
count "n / limit words" (red when over), wrong-school hits listed under the editor with line numbers and an
"intended" toggle, textarea, buttons Evaluate (4b), Save as new version, Discuss in Claude Code; right panel:
rubric results (Structure, Content fit, Programme fit, Accuracy) with rating chips and reasons, the AI-use
policy line, version history with "Compare".
### 4.6 Biography bank — filter buttons by category with counts; list of entries (category label, title,
detail, tags, Anticipated chip, "used in n essays", Edit); Add entry form inline.
### 4.7 Settings — owner name and email, display and reminder zones, data folder path (read-only, from
config), Google: Connect / Connected as …, Sync now (with dry-run checkbox), last sync summary, digest preview.
Phase 5: this month's AI calls and estimated cost (app calls), linking to the AI usage page (§4.9).

### 4.8 Courses and course import (Phase 2c) — reached from Recommenders ("Import courses") and Add professor
- Courses list: name, code, institution, grade, credits, term, lecturers; Import button.
- Import: paste text or choose a .docx / PDF file. A scanned PDF is refused: "This PDF has no text layer; OCR is not supported. Export it as text or Word, or paste the text." Preview table with every field of every row; a grade not found verbatim in the document is flagged and blocks that row; Accept per row and Accept all; nothing is written before. **Review before sending** (DECISIONS #70): Read courses only extracts the text and strips identifiers, then shows exactly the text that would be sent (a scrollable region, each `[removed]` highlighted, with the count) and two actions, **Send to Claude** and **Cancel**; nothing is sent until Send. Its form carries only the stripped text (plus the cleaned file name and the original's sha256); Send strips it again, strictly, before the call. A failed Send shows the review again with the error, so Send can be retried. When nothing was removed the review says "The app found no student number, date of birth or national ID number. Check the text below before sending."; the busy notice on Send reads "Sending the text to Claude Code and reading the courses." (DECISIONS #75). On the preview the actions row (Accept all, Check again) sticks to the bottom from 760 px up, and the page's scroll padding keeps the focused field above it (with JS it follows the bar's measured height; the no-JS fallback is 105 px, enough for the bar on two rows; without JS a focused textarea can still sit partly under the bar, because the browser scrolls it only to its caret line); below 760 px it is not sticky (WCAG 2.4.11; DECISIONS #72, #75). A course's problems stand right under its name (the card's legend); each field a problem is about is marked invalid and described by it, and so is the blocked Accept button. **Check again** with a problem left shows and announces "1 course still has a problem: Course N (name): <problem>." (or "k courses still have a problem. First: …") at the top, with a link to that course (works without JS), and with JS shows that course from its top, focused (DECISIONS #75). With no problem left it shows and announces "Checked again: every course can be accepted." (focus on that notice, shown with the first course below it; #85), or "Checked again: every course is saved." when none is left (#79). Identifiers never appear in the preview. Every field is editable; **Check again** re-runs the checks after a correction; a row already stored is "Already imported". With JS, Send to Claude runs through HTMX with the shared busy state (DECISIONS #63); the form carries only the identifier-stripped text. "Create professors from this document" appears once a row is saved. The Recommenders toolbar has Courses and Import courses.
- Create professors from this document: one line per lecturer (name as written, their courses with the owner's grade, "listed first" marker), sorted by best grade, nothing ticked; a match with an existing professor offers "Link to {name}" instead of creating; Create ticked professors writes them in one batch.

### 4.9 AI usage (DECISIONS #60) — main message: "what each AI call was for, what it cost, and whether it worked"
- Read-only page at `GET /usage` ("AI usage" in the sidebar), from both usage logs (docs/01 §11). There is no
  write route.
- Note, verbatim: "Estimated API-equivalent cost. On your subscription this draws from your monthly Agent SDK
  credit and isn't billed separately unless usage credits are enabled."
- Totals card: "This month is <Month YYYY> in the display zone." Two tables, each with calls and cost this
  month, calls and cost all time, and errors all time:
  - **By source**: all four sources, with zeros for one without calls, plus an "All sources" row, whatever
    the filter.
  - **By action**: follows the source filter; its caption reads "By action · <source>". With no calls from
    that source it shows one row: "No calls from this source yet."
  - A note counts the calls that reported no cost (shown as $0 in the sums).
  - Costs are shown with three decimals everywhere, so the rows add up to the total as shown.
  - Totals don't follow the month filter.
- Filters (a GET form, works without JS; each label stays next to its select): Month (All months by default,
  then every month with calls in the display zone, plus the month asked for) and Source (App by default, Eval,
  Live, Unknown (before the audit log), All sources), with an Apply button. A bad value falls back to the
  default.
- Calls table, newest first:
  - Date and time: display zone, with its abbreviation.
  - Action label.
  - For: the professor's name and the programmes' short names, linked to the sheet panel and the programme
    page. An item no longer in data_dir shows its ID with "(no longer in your data)". Eval and live rows show
    their invented IDs, never resolved or linked. Calls without a subject show "A new programme",
    "A course document", "Skill and lockdown checks" or "Not recorded".
  - Duration ("20.0 s").
  - Estimated cost ("$0.042", or "not reported").
  - Result chip: "Done", or "Error: timed out / Claude Code not found / Claude Code failed / unusable output".
- States:
  - Empty: "No AI calls logged yet. …"; when every line is unreadable: "No readable AI calls."
  - No app calls with the default filters: "No app calls yet. Choose Eval, Live or All sources under Source to
    see the others."
  - Filtered empty: "No calls match these filters."
  - Error banner (role alert): "N lines (or parts) of the AI usage log could not be read", each by file and line
    number (or the whole file) and the field at fault, never its value; "These lines are left out of the list
    and the totals." (plus "The rest of the log loaded normally." only when some lines loaded).
  - Essay subjects show "essay <id>" until Phase 4a can resolve essay titles; a CV for a deleted programme
    shows the ID with "(no longer in your data)".
  - Hint while the old run log's lines aren't imported: "N earlier calls in the old run log (…) aren't in
    this log yet. Run `uv run hub import-usage` to add them."

## 5. States
Every page has: loading (HTMX indicator on the triggering element; a long AI call such as research shows the shared busy state of DECISIONS #63: spinner and busy styling on its aria-disabled button, every other write control on the page aria-disabled and inert, a notice below the actions with an elapsed timer, the limit and what leaving the page does, only the start announced, focus on the result or the error when it ends), empty ("No professors yet — add one or
import a CSV"), error (banner naming the file and field for `DataError`; inline message for failed actions,
e.g. "Claude Code did not respond within 120 s. Nothing was saved."). No raw IDs, "None", "NaN" or stack
traces are ever shown.

## 6. Design tokens (from the mockup)
- Fonts: IBM Plex Sans (400/500/600) for text, IBM Plex Mono 500 for numbers and dates; fallback `system-ui`.
  Loaded from Google Fonts; no other external requests.
- Colours: ground `#F6F7F9`, surface `#FFFFFF`, ink `#15181E`, secondary text `#3F4652`, muted `#5A6170`,
  border `#DDE1E7`, row line `#EEF0F3`, sidebar `#15181E` with hover `#262A33` and text `#C9CDD5`,
  accent `#1F4FD1` (hover `#163A9C`), urgent `#B4480A` on `#FFF1E6` with text `#9A3D06`, success `#1E6B45`
  on `#E7F4EC`, info `#1A3E9E` on `#E8EEFC`, danger `#9B1C1C` on `#FBE9E9`, neutral chip `#3F4652` on `#EEF0F3`.
- Radius 8 px cards, 6 px buttons, 999 px chips. Buttons ≥ 44 px tall. Body 14 px, headings 28/16 px.
- All tokens live in `static/css/tokens.css` as CSS variables; templates never hard-code colours.

## 7. Accessibility
Real `<button>`, `<a>`, `<label>` elements; status cells are buttons with `aria-label="Change Princeton
status for Prof. Mulder"`; menus keyboard-operable; contrast ≥ 4.5:1; colour is never the only signal
(every chip has text). axe: 0 serious/critical violations (C-1-05).
