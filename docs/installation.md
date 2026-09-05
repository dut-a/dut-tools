# Installation

The initial installation model is symlink-based.

Default command directory:

```text
~/.local/bin
```

Install active commands:

```bash
make install
```

Install one:

```bash
make install TOOL=<name>
```

Bootstrap directories, install active commands, and run diagnostics:

```bash
make bootstrap
```

The repository does not modify `.zshrc`, `.bashrc`, or equivalent shell startup files. PATH ownership belongs in `dut-dotfiles`.
