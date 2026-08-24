# Decisions

Append only. Never edit or delete an existing entry. Correct a past decision by appending a new decision. Keep the newest entries at the bottom.

Each entry is exactly two lines. The first carries an ISO 8601 UTC timestamp, **who made the decision** (your GitHub username, `derek` or `andres`), and what changed. The second states what the other side must now do differently - that line is the reason this file exists, so never leave it out.

**Commit this file straight to `main`** - no branch, no pull request. If your push is rejected because
the other side pushed first, run `git pull --no-rebase && git push`; union merge keeps both entries.

## Example entries - delete these before use

- 2026-08-29T14:43Z  derek  auth: switching to JWT, symmetric key from env
  -> other side: stop sending the session cookie, read the Authorization header instead
- 2026-08-29T14:47Z  andres  response: errors now include a stable code field
  -> other side: display the code in diagnostics and do not parse the message text
- 2026-08-29T14:52Z  derek  contract: identifiers are opaque strings
  -> other side: preserve identifier values without numeric conversion
