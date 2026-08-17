---
name: refine
description: Decide what a captured idea should become and spawn the typed entities for it. Use when working the inbox, when someone asks what to do with an idea, or before scheduling anything that is still just a thought.
---

# Refine

An idea is an unjudged thought. Refining is the judgment: what *kind* of
thing is this, and is it one thing or several?

```bash
szsdlc inbox                  # oldest first
szsdlc show IDEA-0001
szsdlc refine IDEA-0001 --into work_item --title "Autosave drafts every 5s"
```

`refine` writes provenance on the child and moves the idea along. Run it
once per entity the idea should become — an idea that is really a
requirement *and* the work to satisfy it spawns two.

## The judgment

Read the idea and ask what it actually is. The project's config names the
available types; the shipped set carves this way:

| The idea is… | It becomes |
|---|---|
| a statement about what the product must do | a requirement |
| a question nobody can answer yet | a spike |
| a change someone could start on Monday | a work item |
| a bundle of related changes with one outcome | an epic |
| a choice between options, with consequences | a decision |

Two failure modes, both common. **Too eager:** turning a vague complaint
into a work item invents a solution nobody chose — refine it into a
requirement or a spike and let the work follow. **Too timid:** spawning an
epic for a one-line fix buys ceremony and no clarity.

## Ideas that go nowhere

Not every idea deserves an entity. Kill it with the reason, so the same
thought arriving next month is recognisable:
`szsdlc drop IDEA-0007 --reason "solved by the caching work in WI-0031"`.

## After spawning

Link what you know: `szsdlc link WI-0001 implements REQ-0004`. Do not
schedule here — placement is `/szsdlc:roadmap`, and it is a decision about
the whole set, not about this one entity.
