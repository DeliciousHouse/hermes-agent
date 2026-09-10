# Guarded block-loop recovery: implementation note

This is a design handoff, not an implemented recovery API. Source references below
are relative to DeliciousHouse/hermes-agent commit
`3049630b7038d0d0d7877c15be886d5cda27ebc0` (the inspected `origin/main`).

## Source-lineage prerequisite

The current fork has the decomposition guard from PR #7, but **does not have**
`Task.block_kind`, `Task.block_recurrences`, `VALID_BLOCK_KINDS`,
`BLOCK_RECURRENCE_LIMIT`, or the typed `block_task` writer. Its only production
`block_loop_detected` reference is the reader in
`hermes_cli/kanban_decompose.py:52`. Do not manufacture those fields in a recovery
helper or claim this base can generate the escalation.

Historical commit `5b5c79a8ef4317146b0db7cc2bbb7495c1748e63`, inspected read-only,
contains the prerequisite typed lifecycle and `tests/hermes_cli/test_kanban_block_kinds.py`.
An explicitly approved in-scope base/port is needed before implementing recovery;
this note does not authorize that port or an upstream PR.

## Existing state and event contracts

- `hermes_cli/kanban_db.py:974`: `tasks` stores status, claim lock/expiry, PID,
  `current_run_id`, failure count/error, and timestamps. `task_links` has the
  `(parent_id, child_id)` primary key. `task_events` has monotonically allocated
  `id`, task ID, nullable run ID, kind, JSON-text payload, and timestamp. There is
  no resolved flag: resolution must be inferred from subsequent events and state.
- In the historical typed lifecycle, additive migration adds nullable TEXT
  `block_kind` and INTEGER NOT NULL DEFAULT 0 `block_recurrences`, also exposed by
  `Task.from_row`. `block_task` only accepts running/ready, optionally guarded by
  `expected_run_id`. Dependency blocks go to todo and emit `dependency_wait`.
  Other kinds go to blocked, or triage at `BLOCK_RECURRENCE_LIMIT` (2 in that
  revision). Same cause means **equal kind, not equal reason text**: increment the
  prior counter for the same kind; start at 1 for a different kind. Dependency
  routing does not increment the counter. Completion clears kind and recurrence.
- The historical loop event payload is `{reason, kind, recurrences, limit}`;
  ordinary typed blocked payload is `{reason, kind, recurrences}`. Both reference
  the closed attempt via `run_id`. Later event producers may add `source_status`;
  it is absent from the historical writer and must not be assumed mandatory.
- `kanban_decompose.py:52,331,514` selects MAX(event ID) per task for loop events,
  excludes it if **any later ordinary blocked event** exists, parses JSON, and
  checks `kind == 'needs_input'`. It protects both direct decomposition and the
  triage list. It does not validate reason, later unblocks/claims, or recovery
  eligibility. A `triage_escalation_recovered` marker alone does not resolve it.
  Reuse its event-order convention, not this weaker reader as mutation authority.
- `kanban_db.py:4385,4553`: decomposition atomically creates tasks, links each new
  task as a **parent of the root**, sets the root to todo, and records
  `decomposed {child_ids, root_assignee}`. The opening docstring describes the
  direction backwards; the SQL is authoritative. The event has no source-loop ID
  or author. The gateway passes `author='auto-decomposer'`
  (`gateway/kanban_watchers.py:963`); created children/events carry that provenance.
- `kanban_db.py:2843,2881`: an ordinary blocked event makes a block sticky until
  unblocked. `recompute_ready` does not promote sticky blocks; otherwise it accepts
  done/archived parents and respects the effective failure limit for blocked tasks.
- `kanban_db.py:4238`: unblock accepts blocked/scheduled, closes a dangling active
  run as reclaimed, clears `current_run_id`, resets `consecutive_failures` and
  `last_failure_error`, and emits `unblocked`. It returns todo if any joined parent
  is not done, else ready. Unlike recompute, this query treats archived parents as
  unfinished. Preserve this existing discrepancy, rather than changing it here.
  The typed revision additionally preserves block kind/recurrences across unblock.
- `kanban_db.py:5636`: `_record_task_failure` owns spawn/crash/timeout accounting
  and `gave_up`. This counter is distinct from block recurrence. Recovery must not
  invoke it or reset either counter; a later explicit unblock still performs its
  existing failure-counter reset.

## Proposed narrow DB surface

Add `recover_block_loop(conn, task_id, *, expected_event_id: int) -> tuple[bool, str | None]`
near `block_task`. True means one materialization; False plus a reason means no
change, including repeated calls. The required source ID prevents an old approval
from accidentally authorizing a newer escalation. This is a DB operation for an
explicit caller, not a new model tool or an automatic unblock loop.

Inside **one** `write_txn(conn)` (`kanban_db.py:1982`, BEGIN IMMEDIATE):

1. Read the task and latest loop event by `ORDER BY id DESC LIMIT 1` for that task.
   Reject missing task/event, nonpositive/non-integer source IDs (including bool),
   or source-ID mismatch. Do not use timestamps or `list_events()[-1]` for freshness:
   list_events sorts by timestamp first and unrelated events can be newer.
2. Parse payload defensively: require a JSON object, exact kind `needs_input`, and
   a string reason with non-whitespace content. Preserve the original reason
   bytes; do not infer consent or a reason from comments/title/body. Require the
   typed task kind/counter to agree with the source generation; mismatches fail
   closed, not repaired. Missing prerequisite schema also fails closed.
3. Reject a later blocked event (already materialized or superseded), a later
   event citing this source as recovered, or any subsequent execution/resolution
   transition: unblock, claim, promote/manual promote, completion, archive,
   specification, review, dependency wait, or failure/reclaim. Use actual event
   names on the implementation base. A conservative initial policy permits only
   `commented`/`heartbeat` after the source, plus the one validated `decomposed`
   transition below; unknown events fail closed. Comments, including an apparent
   approval, do not resolve the generation or bypass any guard.
4. Require status triage with no subsequent decomposition; or todo with exactly
   one subsequent decomposition of this still-unresolved source. For todo, check
   its nonempty `child_ids` against the retained parent links and child creation
   events (`from_decompose_of` this root, `by='auto-decomposer'`), and require at
   least one of those parents still nonterminal (not done/archived). A bare
   `decomposed` marker or unrelated unfinished parent is insufficient. Reject
   ambiguous/manual provenance, removed links, or all-terminal parents. Reject
   every other status, including blocked, ready, running, scheduled, review,
   done, and archived.
5. Require no current run, claim, or worker PID and no unended task run. If the
   source references a run, verify it belongs to this task and is terminal.
   Reject inconsistent run state instead of silently ending another attempt.
   The original escalation already closed its run through `_end_run`
   (`kanban_db.py:2724`), or `_synthesize_ended_run` for an unclaimed task. Do not
   create a new attempt, resurrect a terminal run, or rewrite its handoff.
6. CAS-update only task status to blocked, then call `_append_event`
   (`kanban_db.py:2700`) **once**, with kind `blocked`, source run ID, and payload
   `{reason: original_reason, kind: 'needs_input', recurrences: unchanged_count,
   source_event_id: source.id, source_event_kind: 'block_loop_detected'}`.
   Retain all parent edges, kind, recurrence, failure count/error, assignee,
   timestamps, and terminal run rows. No `block_task` call: it would reject the
   state or count a new recurrence. No promote, unblock, or link-removal detour.

All eligibility reads, source-consumption checks, the CAS, and event append belong
in that transaction. `write_txn` is not nestable; do not call helpers that start
another transaction from inside it. A failed append must roll back the status
update. Concurrent callers serialize; the second observes the consumed source and
must append nothing, even after the first recovery has subsequently been unblocked.

## Notification path to retain

`_append_event` writes the event inside the transaction; it does not send a message.
After commit, `GatewayKanbanWatchersMixin._kanban_notifier_watcher`
(`gateway/kanban_watchers.py:29,79,181,267`) claims ordinary blocked events via
`claim_unseen_events_for_sub` (`kanban_db.py:7377`), renders the original reason,
and uses the existing adapter/cursor/rewind path. Loop events are not in its
notification-kind filter. No direct SQLite maintenance script, bespoke recovery
notification, cursor reset, or subscription replacement is needed. This guarantees
one new event, not exactly-once network delivery; subscriptions and adapters must
already be available, and failed sends can be retried.

## Focused regression work for the implementation card

Reuse the `kanban_home(tmp_path, monkeypatch)` fixtures (isolated HERMES_HOME and
Path.home), real DB helpers, and real connections; do not mutate a live board.

- Extend the typed revision's `test_kanban_block_kinds.py` after its prerequisite
  lands: generate a genuine block/unblock/reblock source; cover triage recovery,
  unchanged memory and runs, later explicit unblock, and same-kind reblock still
  escalating at the existing limit. Do not synthesize the happy-path lifecycle.
- Cover forbidden statuses; missing/malformed payload or reason; non-needs_input;
  old source versus newer escalation; intervening resolution/execution; active,
  dangling, or mismatched runs; repeat recovery before and after unblock. Rejections must
  leave the entire task, links, run rows, and event count unchanged.
- `test_kanban_decompose_db.py`: use real fan-out for legacy todo recovery; check
  child provenance and parent gates, missing/unrelated/terminal parents, and
  preservation of every link. `test_kanban_decompose.py`: retain both needs-input
  exclusion tests and the rule that a marker-only recovery is not resolution.
- `test_kanban_db.py`: extend `test_unblock_with_pending_parents_goes_to_todo`,
  `test_unblock_resets_failure_counters`, and failure-limit promotion coverage.
  Distinguish recovery preserving failures from a later unblock resetting them.
- `test_kanban_core_functionality.py`: retain `test_unblock_invariant_recovery`,
  `test_unblock_normal_path_no_spurious_run`, and stale-run protection.
  `test_kanban_blocked_sticky.py`: recovered block must survive dispatcher ticks,
  even after its parents complete, until explicitly unblocked.
- `test_kanban_notify.py::test_notifier_second_blocked_delivers`: extend with the
  recovered ordinary event and unchanged reason. Assert one cursor-visible event
  and no duplicate on repeated recovery. Exercise two connections competing for
  the same source and inject an append failure to prove atomic rollback.

Current-base tests can validate these existing building blocks, not the absent
recovery operation or historical typed writer. Helper implementation, prerequisite
porting, runtime installation, and live-board recovery are separate work.
