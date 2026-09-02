# Launch: a new project, filed where it belongs

Create a project in the place the reader's own filing convention puts it, with
the state files that make it findable and resumable later. Hand it to
`bootstrap` when it will run as an orchestration.

## Intent

Somebody starting a piece of work should not have to hold the whole filing
scheme in their head. They say what the work is. This operation reads the
convention, settles the place with them, creates the floor, and writes the
project into its parent's index so the next person finds it.

## 0. Resolve the convention

The convention is one markdown file. It says where projects live, what they are
called, what a project directory holds, and where a launch may not go.

Take the first of these that exists:

1. `$PROJECT_CONVENTIONS_PATH`
2. `${XDG_CONFIG_HOME:-~/.config}/project-conventions.md`

First match wins, and nothing merges.

The convention is not an `orchestrate` setting, so it stays out of `config.yaml`
and the machine conf. It describes how somebody files their work, so any skill
that files something can read the same file.

**Neither path resolves, so the launch stops here and §1 runs.** A guessed
location produces a directory nobody else thinks to look in.

Read the file whole before placing anything. It is short by design, and any
clause in it can refuse a launch.

Where the convention is silent on something you have hit, that is an edit to the
convention. Make the edit with them and carry on. A ruling nobody wrote down is
gone by the next launch.

## 1. Establish the convention (first run only)

This is an interview that runs once. It ends with the file §0 could not find.

Show `templates/project-conventions.md` whole, and say that they can change any
of it. A concrete scheme gives somebody something to push against.

Then fill in what the template leaves blank. Ask in small batches, in their
words, and say where your confidence sits as you go. Stop when the answers stop
changing the file.

What the finished file has to answer:

- the root the tree hangs from
- the levels, and what each one is called
- how a project is named
- what a project directory holds
- which file is the index at each level, and what one row in it looks like
- where a launch may not go
- which repository tracks the tree, and what happens to a project's own
  repository

Where a tree already exists, read it first and propose the convention it already
obeys. A convention that contradicts the filing on disk creates a second scheme.

Write the result to `$PROJECT_CONVENTIONS_PATH` when that is set, otherwise to
`${XDG_CONFIG_HOME:-~/.config}/project-conventions.md`. Show them the file, then
carry on with §2.

## 2. Settle the name and the place

Derive the name from the convention's naming rule. The shipped default is
kebab-case with no date in it.

Derive the location from the convention's levels. Propose it and wait for their
yes. The person launching may have no idea which function or family the work
sits under. Placing it is your job.

**A location the convention forbids gets refused, and the refusal quotes the
clause.** Stop, say which sentence of the convention refuses it, and let them
rule. Do not create it anyway and note the deviation.

## 3. Create the floor

Every launch creates these three things.

| What | From |
|---|---|
| the project directory | the place §2 settled |
| `workpaper.md` inside it | `templates/project-workpaper.md` |
| one line in the parent's index | the file and row shape the convention names |

A project missing from its index is findable only by whoever created it. Write
the line at the same time as the directory.

The sentence that goes in the index row is the sentence that opens the
workpaper. Write it once and put it in both places.

## 4. What a launch adds when it applies

- **`plan.md`** for a project running as an orchestration. §5 hands that to
  `bootstrap`, which writes `plan.md` and replaces the seeded workpaper with
  its own fuller one. Seed the workpaper anyway. A handoff that never happens
  leaves the project with state.
- **`action-items.md`** when they ask for one, or when the project already has
  something waiting on a person. Its convention is in
  `reference/human-in-the-loop.md`.
- **Git, said out loud.** Say which repository above the new directory already
  tracks it. Where nothing tracks it, offer to initialize one. Say one of the
  two out loud every time. Where the project carries code, its repository sits
  inside the project directory as `src/` or a name the convention allows, and
  the repository above ignores that directory.

## 5. Hand off

A project that runs as a verifying orchestration goes to
`operations/bootstrap.md`. Its output location is the directory this operation
created, so bootstrap's step 0 is already answered. Say so when you hand over.

Anything else ends here, and the work continues in the workpaper.

## Gate

- the directory sits where the convention derives, and you can quote the clause
  that put it there
- `workpaper.md` exists, seeded, naming the project
- the parent's index carries exactly one new line for this project
- each conditional in §4 was created or declined out loud
- a launch aimed at a place the convention forbids refuses and quotes the clause

Report the path, the index file you wrote into, and which conditionals you took.
