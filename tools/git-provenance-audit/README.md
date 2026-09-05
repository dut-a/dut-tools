# git-provenance-audit

`git-provenance-audit` is the read-only counterpart to `git-context`.

```text
git-context
    declares/selects the identity a repository should use

git-provenance-audit
    verifies which identities Git history actually used
```

It never rewrites history.

## Core invariant

> Audit provenance. Report mismatches. Never mutate commits, refs, remotes, working-tree files, or Git identity configuration.

## Basic usage

```bash
git-provenance-audit
git-provenance-audit ~/dev
git-provenance-audit ~/dev --depth 3
git-provenance-audit ~/dev --commits 100
```

## `git-context` integration

Default identity configuration:

```text
~/.config/git-context/contexts.toml
```

Override it:

```bash
git-provenance-audit --git-context-config ~/.config/git-context/contexts.toml
```

Resolution order:

1. repository-local `git-context.profile` pin;
2. most-specific matching configured path context;
3. otherwise the repository is unclassified.

The audit reads this state only.

## What is checked?

For each selected commit:

- author name and email
- committer name and email

The expected identity comes from the resolved `git-context` profile.

## Filters

```bash
--depth N
--commits N
--branch REF
--author PATTERN
```

`--branch` and `--author` are repeatable. Multiple author filters are ORed and commit IDs are de-duplicated.

## Audit configuration and allowlists

Optional config:

```text
~/.config/git-provenance-audit/config.toml
```

Example:

```toml
version = 1
strict = false

[allow]
emails = ["legacy@example.com"]
names = ["Legacy Build Bot"]

[[allow.identity]]
name = "Release Bot"
email = "release@example.com"

[[repository]]
path = "~/dev/legacy-project"
allowed_emails = ["old@example.com"]
allowed_profiles = ["clients"]
```

Override:

```bash
--config PATH
```

## Strict mode

```bash
git-provenance-audit --strict
```

Strict mode makes an unclassified repository a violation. Classified repository identity mismatches are violations regardless.

## Output

```bash
--format text
--format json
--format csv
```

## Stable exit codes

```text
0 CLEAN
1 VIOLATIONS_FOUND
2 INVALID_INPUT_OR_CONFIG
3 GIT_ERROR
```

## Explicit non-goals

This utility does **not**:

- run `git filter-repo`;
- run `git rebase`;
- amend commits;
- update refs;
- alter author/committer metadata;
- change `user.name` / `user.email`;
- modify `git-context` configuration;
- repair history automatically.
