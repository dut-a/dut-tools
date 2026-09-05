# deploy-pack root Make/checker integration

Add this exact include to the existing root `Makefile`, near the repository's
other extension includes:

```make
-include mk/deploy-pack.inc
```

Do not replace the root help architecture. Add only the help row according to
the repository's existing style/order:

```text
deploy-status-check  Verify deploy-pack deployment state for CI/Make
```

The provided fragment defines:

```make
deploy-status-check:
	@command -v deploy-pack >/dev/null 2>&1 || { \
		printf '%s\n' 'ERROR: deploy-pack is not installed or not on PATH' >&2; \
		exit 2; \
	}
	@deploy-pack deploy status --quiet
```

Semantics:

- exit `0`: deployment state healthy;
- exit `1`: deploy-pack reports unhealthy deployment invariants;
- exit `2`: `deploy-pack` is not installed/on `PATH`.

The checker:

```bash
./scripts/check-deploy-status-target.sh
```

verifies structurally that:

1. the root Makefile includes `mk/deploy-pack.inc`;
2. `deploy-status-check` resolves through Make;
3. the target delegates to `deploy-pack deploy status --quiet`.

Recommended integration into an existing root build-toolchain gate:

```make
check-build-toolchain:
	@...
	@./scripts/check-deploy-status-target.sh
```

or as a separate aggregate prerequisite:

```make
check: deploy-status-check
```

Keep the structural checker separate from the operational status gate:
the checker verifies wiring; `deploy-status-check` verifies current deployment
state.
