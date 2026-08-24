# Decisions

Append only. Never edit or delete an existing entry. Correct a past decision by appending a new decision. Keep the newest entries at the bottom. Each entry is exactly two lines: an ISO 8601 UTC timestamp, the host that made the decision, what changed, and a second line stating what the other side must now do differently.

## Example entries - delete these before use

- 2026-08-29T14:43Z  hostA  auth: switching to JWT, symmetric key from env
  -> other side: stop sending the session cookie, read the Authorization header instead
- 2026-08-29T14:47Z  hostB  response: errors now include a stable code field
  -> other side: display the code in diagnostics and do not parse the message text
- 2026-08-29T14:52Z  hostA  contract: identifiers are opaque strings
  -> other side: preserve identifier values without numeric conversion
