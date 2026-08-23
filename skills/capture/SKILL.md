---
name: capture
description: Record passing thoughts, complaints and half-formed "we should someday…" items as tracked ideas without derailing the current conversation. Use whenever someone says note that down, we should, remind me or worth doing later — and whenever they dump several loose thoughts at once, as a list or a rambling paragraph, rather than asking for one of them to be done now.
---

# Capture

Intake is only worth having if it is cheaper than not using it. One command,
no questions, back to what you were doing.

```bash
szsdlc capture "users keep losing unsaved drafts"
```

It prints the new id. Say the ids in one short clause and carry on with the
task you were on. Do not summarise, do not ask what type it should be, do not
offer to refine now — that is `/szsdlc:refine`, later, on purpose.

## An aside

Something worth doing that is **not** what they asked you to do right now:
"we should really fix that someday", "note that down", "TODO", a complaint
about the system voiced while working on something else, a risk you find
mid-task that is out of scope to fix.

## A dump

Several loose thoughts arriving at once — numbered, bulleted, or one paragraph
that changes subject twice — is intake, not a brief. Not fleshed out enough to
act on is the definition of the case, not a reason to wait.

Capture **every** item, one command each, before you reply. Reading a dump is
not capturing it: your context ends with the session and the ideas must not.
When a dump also carries one thing to do now, do that thing *and* capture the
rest.

## Not capture

The task you are currently doing — already tracked. A step in your own
working notes. Anything they are actively asking you to do this turn.

## Writing the text

What a future reader gets. One sentence, in their words, stating the problem
rather than a solution: "drafts are lost on refresh" survives, "add autosave"
pre-commits to an answer nobody has judged. One idea per capture — that is
what makes them refinable independently. Before capturing, `szsdlc inbox` if
you have not seen it this session; the same thought already there means say so
and skip it, because two ids for one thought is worse than none.
