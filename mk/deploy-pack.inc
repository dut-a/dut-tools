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

# BEGIN DEPLOY-PACK-CLOSEOUT-AUTOMATION-01
.PHONY: deploy-prepare deploy-closeout deploy-closeout-status

DEPLOY_PACK_CLOSEOUT_SCRIPT ?= tools/deploy-pack/scripts/closeout.sh

# Prepare archive + signed production verifier in a sibling closeout directory.
# Optional: REF=<git-ref> TTL=60 LANGUAGE=php OUT_DIR=/path/to/session
deploy-prepare:
	@$(DEPLOY_PACK_CLOSEOUT_SCRIPT) prepare \
		--ref "$${REF:-HEAD}" \
		--ttl "$${TTL:-60}" \
		--language "$${LANGUAGE:-php}" \
		$${OUT_DIR:+--out-dir "$$OUT_DIR"}

# Finish after production returns signed evidence.
# Required: EVIDENCE=/path/to/signed-evidence.json
# Optional: SESSION=/path/to/deploy-pack-closeout-<ref>
deploy-closeout:
	@test -n "$(EVIDENCE)" || { echo "ERROR: EVIDENCE=/path/to/signed-evidence.json is required" >&2; exit 2; }
	@$(DEPLOY_PACK_CLOSEOUT_SCRIPT) finish \
		--evidence "$(EVIDENCE)" \
		$${SESSION:+--session "$$SESSION"}

deploy-closeout-status:
	@$(DEPLOY_PACK_CLOSEOUT_SCRIPT) status $${SESSION:+--session "$$SESSION"}
# END DEPLOY-PACK-CLOSEOUT-AUTOMATION-01

# BEGIN DEPLOY-PACK-HELP-SURFACE-01
.PHONY: deploy-help

deploy-help:
	@$(DEPLOY_PACK_CLOSEOUT_SCRIPT) --help
# END DEPLOY-PACK-HELP-SURFACE-01
