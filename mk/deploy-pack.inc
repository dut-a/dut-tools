# deploy-pack root Make integration
#
# Include from the repository root Makefile:
#
#   -include mk/deploy-pack.inc
#
# This fragment is intentionally extension-safe and does not own the root help
# architecture. Add the help row using the repository's established convention.

.PHONY: deploy-status-check deploy-pack-test deploy-pack-install

deploy-status-check:
	@command -v deploy-pack >/dev/null 2>&1 || { \
		printf '%s\n' 'ERROR: deploy-pack is not installed or not on PATH' >&2; \
		exit 2; \
	}
	@deploy-pack deploy status --quiet

deploy-pack-test:
	@$(MAKE) -C tools/deploy-pack test

deploy-pack-install:
	@$(MAKE) -C tools/deploy-pack install
