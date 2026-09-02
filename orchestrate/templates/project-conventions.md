---
status: DRAFT
---

# Project conventions

> **DRAFT.** This is the shipped recommendation. It is here so a first launch
> has something to push against. Change any of it, or replace the file. The
> `launch` operation reads this file and obeys what it says, so an edit here
> changes the next launch.

Where projects live, what one is called, what a project directory holds, and
where a launch may not go.

## The tree

Root: `{{the directory the whole tree hangs from}}`

Work is filed under **functions**. A function is a standing area that never
finishes.

| Function | Holds |
|---|---|
| `strategy` | vision, strategy, and other meta processes |
| `operations` | doing and delivering the work itself |
| `growth` | reaching the people the work is for |
| `back-office` | finance, legal, and other administrative work |

These four are a starting shape for any body of work a person or a group runs.
Rename them to your own words, or cut the level out.

A function may hold **project families**. Use one where a function runs several
lines of work and each line carries its own projects. A family is a directory
shaped like a function. Skip the level when the projects under a function do
not need grouping.

A **project** is the leaf. It is the thing that finishes.

## Naming

Kebab-case. Say what the work is.

No dates in a name. The directory already records when it was made.

## What a project directory holds

The files that track a project sit directly in its directory.

| Path | Created |
|---|---|
| `workpaper.md` | always. Live state and the journal. |
| `plan.md` | when the project runs as an orchestration |
| `action-items.md` | when the project is waiting on a person |

Code sits in a repository nested inside the project directory, as `src/` or a
name that says what it is. The repository above ignores that directory. A
project with no code has no such directory.

## The index

Every directory that holds projects carries an index:
`{{name the file, and show one row}}`

Each launch writes one line for the new project: its name, and a sentence
saying what it is.

Somebody who did not create a project finds it through the index.

## Where a launch may not go

`{{The clauses a launch has to refuse. For example: never a project sitting
directly at the root; never inside another project's repository; never a second
directory for work that already has one.}}`

## Git

`{{Which repository tracks this tree, whether a nested project repository is
initialized at launch, and whether it gets a remote.}}`
