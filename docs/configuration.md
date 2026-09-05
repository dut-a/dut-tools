# Configuration

Default precedence:

```text
CLI
>
environment
>
project configuration
>
user configuration
>
built-in defaults
```

Use TOML for new human-maintained configuration unless the target ecosystem has a stronger native convention.

Suggested user-level location:

```text
~/.config/dut/
```

Suggested project-level location:

```text
.tooling/
```

Company policy should not be embedded in a generic Dut-owned utility. The utility provides mechanism; the consuming project or organization provides policy.
