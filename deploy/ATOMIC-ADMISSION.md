# Atomic admission protocol prototype — integration still required

This PR implements and tests an isolated SQLite transaction primitive for a
maintenance release gate. `admitted_transaction` takes `BEGIN IMMEDIATE`,
reads the durable marker, and yields that **same connection** for inserting
the task row before commit.

The critical invariant is: **checking the marker and inserting the task must
be in the same SQL write transaction**. Checking the flag separately and
creating the task later leaves a race window. Concurrent maintenance begin
operations serialize against a held admission transaction.

Tests cover admission before maintenance, rejection after maintenance,
transaction rollback and a concurrent maintenance begin that must wait for
an in-progress task insertion to commit.

## What is not implemented

- Not connected to the existing task repositories or API endpoints.
- No cross-repository transaction adoption for manual tasks or launch jobs.
- No task drain/wait protocol, background scheduler pause, or in-flight
  external OCI API call tracking.
- No operator activation interface or production deployment.

**Do not treat this PR as an operationally safe maintenance mode.**
Application-level integration needs explicit review of call paths to avoid
nested SQLite transaction deadlocks and duplicate tasks.
