# Conversation-history branch evaluation

**Evaluation target:** the assistant chat screen in `apps/agentic-assistant-web`.

**Date:** 2026-09-14

## Executive summary

`feat/conversation-history-raw` is the best starting point, but none of the
three branches is merge-ready without follow-up fixes.

| Branch | Functional | Backend | Frontend | Security | Tests | Docs | Total |
|---|---:|---:|---:|---:|---:|---:|---:|
| `feat/conversation-history-raw` | 25/30 | 15/20 | 16/20 | 4/10 | 12/15 | 4/5 | **76/100** |
| `feat/conversation-history-codenib-withskills` | 26/30 | 10/20 | 15/20 | 7/10 | 11/15 | 5/5 | **74/100** |
| `feat/conversation-history-graphify` | 24/30 | 14/20 | 12/20 | 9/10 | 8/15 | 4/5 | **71/100** |

## Findings common to all branches

- All branches scope SQL queries using the authenticated subject.
- All reuse `GET /conversations/{id}` and `loadHistory()` before continuing through the existing chat socket.
- All normalize and truncate derived previews without adding a database migration.
- None uses deterministic ordering for equal timestamps. They need `ORDER BY updated_at DESC, conversation_id DESC`.
- None implements real pagination.
- None adds frontend unit or component tests.
- Existing backend history tests cover 404/403 behavior, but frontend handling is generic and does not test status-specific failures.
- All branches passed backend lint, assistant-web ESLint, TypeScript checks, and `git diff --check`.

## Branch-specific findings

### `feat/conversation-history-raw`

Strengths:

- Uses TanStack Query and a shared `QueryClientProvider`.
- Uses one database round trip with grouped counts and a derived first-message preview.
- Provides loading, retry, empty, reopen-loading, and reopen-error states.
- Has the largest focused backend test set: 20 tests passed.
- Lowest reported token usage.

Risks:

- The query key is static: `['assistant', 'conversations']`. It is not scoped to the authenticated user. After account switching, cached conversations from the previous user could be displayed for up to the configured stale period.
- The list is hard-capped at 50 and does not expose pagination.
- Refresh happens when the conversation ID changes, not after every completed follow-up turn, so an existing chat’s turn count may remain stale.
- No frontend tests were added.

### `feat/conversation-history-codenib-withskills`

Strengths:

- Richest UX: collapsible panel, selection loading state, retry, empty state, reopen failure state, and responsive layout.
- Strongest documentation. It documents the API contract, Option A, UI flow, and test locations.
- Exposes a validated `limit` query parameter from the API route.
- 19 focused backend tests passed.

Risks:

- `async_list_conversations()` performs an N+1 query pattern: one list query plus count and preview queries per conversation.
- The route exposes `limit`, but not `offset` or a cursor, so this is bounded retrieval rather than pagination.
- Preview truncation is length-safe but does not append an ellipsis, making truncation ambiguous to users.
- Uses a custom `useState`/`useEffect` hook instead of the repository’s TanStack Query pattern.
- The hook retains old summaries while the authenticated token changes, creating a transient cross-account display risk.
- No frontend tests were added.

### `feat/conversation-history-graphify`

Strengths:

- Uses one database round trip with correlated count and preview subqueries.
- Preview truncation includes an ellipsis.
- The custom hook gates displayed rows on the token that fetched them, avoiding the stale-token display issue found in the other two branches.
- Existing conversation and chat tests passed: 20 tests.

Risks:

- The list query has no `LIMIT`, making response size and database work unbounded.
- No actual pagination.
- Reopening a chat has no dedicated loading indicator; the panel only disables rows while the chat is busy.
- No dedicated `test_conversation_list.py` file; list coverage is mixed into existing tests.
- Uses a custom `useState`/`useEffect` hook instead of TanStack Query.
- No frontend tests were added.

## Evaluation criteria

### Functional correctness — 30 points

- Owner-only listing from authenticated claims.
- Newest-first ordering with deterministic tie handling.
- Correct conversation ID, timestamp, turn count, and first-message preview.
- List → click → existing history API → continue through the existing socket.
- Empty, loading, retry, 404, and 403 behavior.
- Safe preview normalization and truncation.

### Backend quality and scalability — 20 points

- Route logic separated from database access.
- Single-query or bounded-query behavior; identify N+1 patterns.
- Actual pagination versus merely applying a fixed limit.
- Appropriate indexes or documented assumptions.
- No unnecessary schema migration for Option A.

### Frontend architecture and UX — 20 points

- Shared API client and query-hook pattern.
- No direct `fetch` inside components.
- Authenticated-query cache isolation between users.
- Loading state while reopening a row.
- Responsive/collapsible panel, disabled states, accessible labels, and stable empty/error states.
- Refresh behavior after new chats and follow-up turns.

### Security and failure isolation — 10 points

- No model-supplied owner identity.
- No cross-user conversation leakage.
- 404/403 behavior does not reveal unauthorized chat existence.
- Query caches cannot display a previous user’s history after account switching.

### Tests — 15 points

- Backend owner-scope, newest-first, preview, count, limit/pagination, auth, and response-shape tests.
- Existing single-history 404/403 regression coverage.
- Frontend panel, hook, retry, empty, selection, and reopen-failure tests.
- Build, lint, and type-check coverage.

### Documentation — 5 points

- API contract, Option A decision, UI behavior, error states, and test locations documented in the same branch.

## Validation results

The branches were evaluated from disposable snapshots so the main worktree was
not changed.

| Branch | Backend focused tests | Backend Ruff | Frontend ESLint | Frontend TypeScript |
|---|---:|---|---|---|
| `codenib-withskills` | 19 passed | Passed | Passed | Passed |
| `graphify` | 20 passed | Passed | Passed | Passed |
| `raw` | 20 passed | Passed | Passed | Passed |

The focused backend command was:

```bash
uv run python -m pytest tests/test_conversation_list.py tests/test_conversations.py tests/test_chat_api.py -q
```

`graphify` has no `tests/test_conversation_list.py`, so its existing
conversation and chat tests were run separately:

```bash
uv run python -m pytest tests/test_conversations.py tests/test_chat_api.py -q
```

The normal `pnpm lint` wrapper could not run inside disposable snapshots
because pnpm attempted a workspace install and hit its SQLite store
restriction. The equivalent installed ESLint and TypeScript binaries passed
for all three branches.

No frontend test files were found in any branch.

## Efficiency report

| Branch | Reported total tokens |
|---|---:|
| `feat/conversation-history-codenib-withskills` | 144k |
| `feat/conversation-history-graphify` | 136k |
| `feat/conversation-history-raw` | 112k |

Input/output split, tool-call count, and time-to-done were not available from
Git evidence. Total token usage was reported separately and was not used as a
proxy for code quality.

## Recommendation

Start from `feat/conversation-history-raw`, then fix the following before
merging:

1. Scope the React Query key by a stable authenticated user identifier, or clear the conversation cache on logout.
2. Add deterministic timestamp tie ordering.
3. Implement cursor or offset pagination instead of relying on a fixed limit.
4. Refresh the list after every completed turn, including follow-ups to an existing conversation.
5. Add frontend panel and hook tests covering loading, empty, retry, selection, 404, and 403 behavior.
6. Add or verify an index supporting owner-scoped newest-first listing if production history volume warrants it.

Option B, stored titles, is not justified by these branch diffs. Option A is
appropriate until derived-preview query cost is measured at realistic history
volume.
