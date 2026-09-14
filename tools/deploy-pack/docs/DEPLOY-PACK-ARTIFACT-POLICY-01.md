# DEPLOY-PACK-ARTIFACT-POLICY-01 — Explicit Artifact Deployment Surface

Artifact mode supports an explicit project policy in `.deploy-pack.toml`.

```toml
[artifact]
include = [
  ".htaccess",
  "*.php",
  "css/**",
  "gfx/**",
  "images/**",
  "inc/**",
  "js/**",
]
exclude = [
  "dist/**",
  "tools/**",
  "artifacts/**",
]
```

## Semantics

- Hard artifact-hygiene exclusions remain non-overridable.
- `exclude` always narrows the artifact surface.
- A non-empty `include` list switches artifact mode to allowlist semantics.
- `*` matches inside one path segment; `**` crosses directory boundaries.
- `foo/**` matches `foo` itself and every descendant.
- Required paths must exist and survive the final artifact policy.
- Artifact policy is independent of Git-aware `[pack]` policy and `.gitignore`.
- The invocation directory's `.deploy-pack.toml` is preferred. If absent,
  `.deploy-pack.toml` directly inside `--source` is used.
- With no `[artifact]` table, artifact mode keeps its hygiene-only behavior.

## Recommended posture

When `--source` is a repository root, prefer an explicit `include` allowlist.
When `--source` is already a dedicated build output such as `dist/`, an
exclude-only policy or hygiene-only mode may be sufficient.
