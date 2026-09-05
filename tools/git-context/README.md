# git-context

`git-context` selects Git identity and related host/account context from the repository's filesystem location.

The core rule is deliberately boring and reliable:

> Path selects context. More specific paths override broader paths.

There is **no fallback personal identity** by default. An unclassified repository should fail closed rather than accidentally receive the wrong author identity.

## Version

```bash
git-context --version
```

Current monorepo baseline: `1.5.0`.

## Default configuration

Source of truth:

```text
~/.config/git-context/contexts.toml
```

Generated files:

```text
~/.config/git-context/generated/
├── allowed_signers
├── git-context.gitconfig
└── profiles/
    ├── clients.gitconfig
    └── tembeek.gitconfig
```

Backups:

```text
~/.config/git-context/backups/
```

Host CLI isolation:

```text
~/.config/git-context/host-cli/
├── github/<account>/
└── gitlab/<account>/
```

## Initialize

```bash
git-context init
```

This creates a safe starter configuration and canonical workspace roots if they do not already exist.

## Inspect context

```bash
git-context current
git-context current ~/dev/tembeek/tembeek-platform
git-context explain ~/dev/clients/acme/project
```

## Apply generated Git configuration

```bash
git-context apply
```

`apply`:

1. validates `contexts.toml`;
2. generates per-profile Git config;
3. generates one managed include file;
4. sets `user.useConfigOnly=true`;
5. removes global `user.name` / `user.email` after backing them up;
6. adds one managed include to the global Git config.

The generated configuration uses Git `includeIf "gitdir:..."` rules.

## Manual profile pinning

For exceptional repositories:

```bash
git-context pin tembeek
git-context unpin
```

The pin is repository-local and does not modify global identity.

## Machine bootstrap

```bash
git-context machine bootstrap
git-context machine bootstrap --install-tools
git-context machine bootstrap --provision-keys
```

Tool installation and key provisioning are explicit opt-ins.

Machine bootstrap can create:

- workspace directories;
- generated profile config;
- SSH include scaffolding;
- isolated `gh` / `glab` account directories;
- shell completion directory.

## Host CLI isolation

```bash
git-context host path github tembeek
git-context host path gitlab client-a
```

`enter` emits shell exports and a directory change:

```bash
eval "$(git-context enter ~/dev/tembeek/tembeek-platform)"
```

Typical output includes isolated `GH_CONFIG_DIR` or `GLAB_CONFIG_DIR` values where configured.

## Repository audit

```bash
git-context audit ~/dev
```

The audit reports repository path, resolved context, local pin, and whether the repo is dirty.

## Relocation

Relocation is **plan-first**:

```bash
git-context relocate ~/old/repo ~/dev/tembeek/repo
```

No move occurs without:

```bash
git-context relocate ~/old/repo ~/dev/tembeek/repo --apply
```

By default it refuses:

- dirty repositories;
- destination collisions;
- moves outside the home directory.

Override dirty-tree refusal only deliberately:

```bash
git-context relocate ... --allow-dirty --apply
```

## Default example

The starter config includes placeholders consistent with the established scheme:

```toml
version = 1

[profiles.tembeek]
name = "Dut Athian"
email = "dut.dev@tembeek.com"

[profiles.clients]
name = "Dut Athian"
email = "dut.clients@tembeek.com"

[[contexts]]
name = "tembeek"
root = "~/dev/tembeek"
profile = "tembeek"

[[contexts]]
name = "clients"
root = "~/dev/clients"
profile = "clients"
```

Edit those values when your real directory convention differs.
