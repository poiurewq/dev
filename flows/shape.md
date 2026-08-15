# Shape flow — cut many threads onto the board

Entry: **only** when `/dev add` triage picks the shape path (SKILL.md
**Adding work**). Many threads must be cut *together* before tasks are
knowable — a new product, a first iteration, or a large new subsystem.
Not a new verb. Do not load this file from plan.

The board is the only artifact. No standing spec, no session-break draft.
If they leave without filing, nothing is written.

## Converse

Follow attention. Do not open with a template, a tour of axes, or a
checklist. Prefer their dump: cluster it and talk about the interesting
bits. Ask only when the board would be incoherent without an answer — a
constitutive fork (which tasks exist, or how they depend), or a pile that
is clearly more than one iteration with no cut.

Speak in **their** frame. If they say MVP, beta, "the launch," "this
month," use that. Never introduce v0 / first-cut / MVP of your own.

Local forks (library, naming, error style, sync vs async internals) stay
implement-time, as today.

## Settlement

When you are ready to file — or they ask to see the cut — put a
settlement **in the chat**, in their words. Every heading is optional if
it did not come up:

```text
What this is
<their words, 1–2 sentences>

This iteration          ← or whatever they called the current cut
- …

Later
- …

Choices that change what gets filed
- F: (a) … (b) …   leaning (a), because <what they said, or "your call">

Tasks
- umbrella: …
  - T…  deps: …
```

No "not this product" unless they named one. No Areas block unless names
emerged. No Forks block if nothing constitutive came up.

This is a proposal, not a file. Happy path: they say file (or "once more,
then file"), you revise if asked, then write the board. They may keep
marking it up in this conversation — same posture as review. Do not start
agent-driven loops (personas, "one more review round," a second session).

Prefer mid-sized this-iteration tasks over fifty leaves; overflow is
`later`, not more backlog. Don't hang later work off a this-iteration
umbrella. A later cluster may have its own later umbrella.

## File

Only on explicit go-ahead. Then:

1. If area names emerged, `TASKS area set` them (founding is when areas
   may appear before the usual ~3-task cluster).
2. `TASKS related "<title + desc>"` per drafted task. Fold duplicates;
   wire deps the neighbours imply (either direction).
3. This-iteration leaves first (`TASKS add`, default backlog), umbrella
   last with `--kind umbrella --deps <child-ids>`. Umbrella body = the
   current cut in a few sentences + constraints that mattered — not a
   restatement of the later shelf.
4. Later threads: `TASKS add --status later` (and a later umbrella if a
   parked cluster is itself a goal).
5. Show `TASKS board`. Stop proposing structure. Don't auto-offer
   `/dev meta` after every add; a one-liner after a large filing is
   enough.

Coverage of the current cut is the this-iteration umbrella's job at
iteration close. Later tasks are not its children.
