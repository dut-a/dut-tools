# bin

This directory contains stable user-facing command facades for **active** tools.

The initial registered tools are placeholders, so no fake command wrappers are created yet. Add `bin/<command>` only when the authoritative implementation has been migrated and the tool is changed to `status = "active"` in `tools.toml`.
