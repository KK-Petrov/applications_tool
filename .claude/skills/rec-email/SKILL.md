---
name: rec-email
description: Draft a formal email asking a professor for a letter of recommendation, from the app's JSON payload (Application Hub). Use as /rec-email <professor_id>.
argument-hint: <professor_id>
disable-model-invocation: true
---

# Recommendation request email

You write one email in which the applicant (the **owner**) asks a professor for a letter of
recommendation for master's programmes in finance. The owner will send it after light edits, so it must
read as his own formal, careful writing, and every fact in it must come from the input.

## Input
A JSON document (in the app it is on stdin) with:
- `owner.name`: the applicant, who signs the email.
- `professor`: `title` and `surname` (use exactly as given; `surname` is already the form that stands
  after the title, e.g. "Van der Berg" for a name written "van der Berg", so don't warn about its
  capitalisation), `name`, `university`, `courses` (each with
  `name`, `description`, `grade`, `term`; null means unknown), `interaction` (something specific the owner
  did with this professor, or null) and `tone` (`formal`, or `warm` for a slightly warmer formal register).
- `programmes`: the programmes this request is for, each with `short_name`, `full_name`, `deadline_text`
  and `letters_note`.
- `biography`: the owner's background entries (`id`, `category`, `title`, `detail`, `state`, `dates`).
  `state: anticipated` means not yet achieved.
- `constraints`: `min_words`, `max_words`, `subject` and `sign_off`.

If you were started interactively with a professor id instead (e.g. `/rec-email mulder`), run
`uv run hub email-payload <id>` to get this JSON (add `--programme <programme_id>` for each programme to
include, else it uses the professor's To ask and Draft ready programmes). Then write the email and show
the subject and body in the chat. Don't save or send anything; the owner copies it.

## Rules
1. **Salutation and close.** The first line is exactly `Dear {title} {surname},` with the payload's
   `professor.title` and `professor.surname` as given (e.g. `Dear Dr. De Vries,`; never "Prof." for
   "Professor"). The email ends with `constraints.sign_off` on its own line, then `owner.name`.
2. **Subject.** Copy `constraints.subject` exactly. The owner chose it for every request, also one with a
   single programme; don't comment on it.
3. **Structure**, in this order, as short paragraphs:
   a. Who the owner is (only from biography education entries, if any) and the course he took with the
      professor: write each course `name` exactly as given, character for character, including any code
      in brackets; add the term and grade only if given.
   b. What the course covered and what the owner took from it, drawn from the course `description` and
      the `interaction`. Describe the course's content; don't claim results, projects or performance that
      the input doesn't state. With a short description, stay with the topics it names: say that the
      course covered them and that the owner wants to build on them, and no more (no invented lessons or
      insights).
   c. Why these programmes, only from biography `goal` and `experience` entries. With none, keep this to
      one general sentence about his interest in quantitative finance and add a warning.
   d. The programmes and deadlines as a short list, one line each, exactly in this form:
      `- {short_name}: {deadline_text}`. Copy both character for character: never reformat, convert or
      shorten a date, time or zone.
   e. Offer his CV, transcript and a short summary of points the professor could speak to; ask whether
      the professor feels able to write a strong letter; thank the professor.
4. **Length.** The whole body, from `Dear` to the owner's name, has between `constraints.min_words` and
   `constraints.max_words` words; aim for the middle (about 230). Budget: (a) 35–50 words, (b) 50–70,
   (c) 30–45, (d) the list, (e) 40–55. A word is anything between spaces.
5. **Only facts from the input.** Never invent or embellish grades, rankings, class sizes, projects,
   papers, conversations, office hours, praise, awards, work experience, or anything about the professor's
   own research. Statements of interest, thanks and offers are fine; facts are not unless the input
   states them. Ignore placeholder text in square brackets such as `[number of letters to verify]`.
   The input has no today's date: give dates as written (`Fall 2026`, `Jan–Jun 2026`) and never place
   them relative to now ("currently", "last year", "this summer").
6. **Anticipated entries** are written as expected, in the same sentence: "I expect to graduate cum laude
   in June 2027", "I am on track to …". Never state them as achieved.
7. **Only the programmes in the input.** Never mention any other programme, school or university you
   might know of, nor how many programmes the owner applies to overall.
8. **Gaps become warnings, not inventions.** If a grade, term, interaction or goal entry is missing,
   write around it and add a short warning in plain words for the owner, e.g. "No specific interaction
   given, so the paragraph about the course relies on the course description only." Never use paragraph
   letters or input field names (such as `letters_note`) in a warning.
9. **Register.** Formal British-leaning English, first person, no exclamation marks, no flattery. With
   `tone: warm`, allow one warmer sentence (e.g. that he valued the course), still formal.
10. **Fresh wording for each professor.** The owner sends many of these emails, so word the opening and
    closing sentences anew each time, fitted to this professor and course: no fixed stock sentence, and
    don't describe a biography entry (e.g. the research-assistant role) with the same sentence you would
    use in any other email. The five-part structure, the list format and every fact stay exactly as
    specified. Don't reuse sentences from the example below; it shows the structure and register only.
11. **Months in full.** Write every month out ("from January to June 2026", "June 2027"), also when the
    input abbreviates it (`Jan–Jun 2026`); never change the date itself. The deadline lines are copied
    from `deadline_text` as given.
12. **Output** only the structured result: `subject`, `body` (plain text with line breaks, no Markdown
    other than the `- ` list), `used_biography_ids` (ids of the biography entries the body uses) and
    `warnings` (each a short sentence; empty if none).

## Example (invented data; structure and register only, don't reuse its sentences)
Input (shortened): owner "Sam Example"; professor title "Dr.", surname "Okafor", course
"Empirical Asset Pricing (FIN-4410)", term "Spring 2026", grade "8.5", description "Factor models,
portfolio sorts and Fama–MacBeth regressions; students replicate a published factor in a group project",
interaction "Discussed my replication of the momentum factor in office hours"; one programme
`Northfield MFin`, deadline_text "1 December 2026, 11:59 pm ET"; biography: `bsc` (education, "BSc
Econometrics, Example University", dates "2023–2027"), `goal-quant` (goal, "Work in quantitative asset
management", detail "Build and test systematic strategies"), `cum-laude` (honour, "Cum laude",
anticipated, "Jun 2027").

Every sentence below traces to that input; the course paragraph restates the description and the
interaction and adds no lesson of its own.

Output body:

```
Dear Dr. Okafor,

I am studying for a BSc in Econometrics at Example University (2023–2027), and in Spring 2026 I took your course Empirical Asset Pricing (FIN-4410), in which I obtained a grade of 8.5. I expect to graduate cum laude in June 2027.

The course took us through factor models, portfolio sorts and Fama–MacBeth regressions, and in the group project we replicated a published factor. I chose the momentum factor for my replication and discussed it with you during office hours. I would like to take this empirical work further at master's level.

My aim is to work in quantitative asset management, building and testing systematic strategies, and a master's programme in finance is the step I see towards that. I have listed the deadline below so that you can judge whether the timing suits you.

The programme and its deadline:
- Northfield MFin: 1 December 2026, 11:59 pm ET

If it would help, I can send you my CV, my transcript and a short list of points from your course that you could draw on. Would you be willing to write a strong letter of recommendation for this application? I would also be happy to meet or to answer any questions about my plans. I realise that this asks for your time, and I would be grateful for your consideration either way.

Kind regards,
Sam Example
```

`used_biography_ids`: ["bsc", "cum-laude", "goal-quant"]; `warnings`: [].
