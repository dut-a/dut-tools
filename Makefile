SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

PYTHON ?= python3
PREFIX ?= $(HOME)/.local
BIN_DIR ?= $(PREFIX)/bin
TOOL ?=
VERSION ?=

BOLD := \033[1m
DIM := \033[2m
CYAN := \033[36m
GREEN := \033[32m
YELLOW := \033[33m
RESET := \033[0m

.PHONY: help list versions bootstrap install uninstall doctor test check lint release release-gate changed-tools validate-release-tag package-distribution distribution-smoke clean

help: ## Show this help
	@printf "$(BOLD)Dut Tools$(RESET)\n"
	@printf "$(DIM)Cross-domain, independently versioned Dut-owned utilities.$(RESET)\n\n"
	@printf "$(BOLD)Usage$(RESET)\n"
	@printf "  make $(CYAN)<target>$(RESET) [TOOL=name] [VERSION=x.y.z] [PREFIX=path]\n\n"
	@printf "$(BOLD)Discovery$(RESET)\n"
	@printf "  $(GREEN)help$(RESET)        Show this help\n"
	@printf "  $(GREEN)list$(RESET)        List registered tools and lifecycle status\n"
	@printf "  $(GREEN)versions$(RESET)    Show per-tool versions\n\n"
	@printf "$(BOLD)Setup$(RESET)\n"
	@printf "  $(GREEN)bootstrap$(RESET)   Prepare local directories, install active tools, run doctor\n"
	@printf "  $(GREEN)install$(RESET)     Symlink active tool commands into BIN_DIR\n"
	@printf "  $(GREEN)uninstall$(RESET)   Remove dut-tools symlinks from BIN_DIR\n"
	@printf "  $(GREEN)doctor$(RESET)      Validate host/runtime/repository health\n\n"
	@printf "$(BOLD)Quality$(RESET)\n"
	@printf "  $(GREEN)test$(RESET)        Run repository structural tests, or one tool's tests\n"
	@printf "  $(GREEN)check$(RESET)       Run registry validation, lint, and tests\n"
	@printf "  $(GREEN)lint$(RESET)        Run lightweight repository lint checks\n\n"
	@printf "$(BOLD)Release$(RESET)\n"
	@printf "  $(GREEN)release$(RESET)     Prepare TOOL release at VERSION (does not tag without APPLY=1)\n\n"
	@printf "$(BOLD)Maintenance$(RESET)\n"
	@printf "  $(GREEN)clean$(RESET)       Remove local generated/test cache files\n\n"
	@printf "$(BOLD)Examples$(RESET)\n"
	@printf "  make list\n"
	@printf "  make install\n"
	@printf "  make install TOOL=git-context\n"
	@printf "  make test TOOL=context-zip\n"
	@printf "  make test VERBOSE=1\n"
	@printf "  make test QUIET=1\n"
	@printf "  make release TOOL=git-context VERSION=1.5.0\n"
	@printf "  make release TOOL=git-context VERSION=1.5.0 APPLY=1\n"

	@printf "  $(GREEN)release-gate$(RESET)  Run full dut-tools release readiness gate\n"
	@printf "  $(GREEN)changed-tools$(RESET)  Show tools affected by BASE...HEAD\n"
	@printf "  $(GREEN)validate-release-tag$(RESET)  Validate TAG=<tool>/v<version>\n"
	@printf "  $(GREEN)package-distribution$(RESET)  Build cumulative ZIP (OUT=...)\n"
	@printf "  $(GREEN)distribution-smoke$(RESET)  Validate extracted ZIP (ARCHIVE=...)\n"
list:
	@$(PYTHON) scripts/registry.py list

versions:
	@$(PYTHON) scripts/registry.py versions

bootstrap:
	@BIN_DIR="$(BIN_DIR)" ./scripts/bootstrap $(if $(TOOL),--tool "$(TOOL)",)

install:
	@BIN_DIR="$(BIN_DIR)" ./scripts/install $(if $(TOOL),--tool "$(TOOL)",)

uninstall:
	@BIN_DIR="$(BIN_DIR)" ./scripts/uninstall $(if $(TOOL),--tool "$(TOOL)",)

doctor:
	@BIN_DIR="$(BIN_DIR)" ./scripts/doctor

test:
	@VERBOSE="$(VERBOSE)" QUIET="$(QUIET)" ./scripts/test $(if $(TOOL),--tool "$(TOOL)",)

lint:
	@./scripts/lint

check:
	@./scripts/check

changed-tools:
	@if [[ -z "$(BASE)" ]]; then echo "ERROR: BASE=<git-ref> is required"; exit 2; fi
	@python3 scripts/changed-tools --base "$(BASE)" --head "$(if $(HEAD),$(HEAD),HEAD)"

validate-release-tag:
	@if [[ -z "$(TAG)" ]]; then echo "ERROR: TAG=<tool>/v<version> is required"; exit 2; fi
	@python3 scripts/validate-release-tag "$(TAG)"

release-gate:
	@./scripts/release-gate

release:
	@if [[ -z "$(TOOL)" || -z "$(VERSION)" ]]; then \
		echo "ERROR: release requires TOOL=<name> VERSION=<x.y.z>"; \
		exit 2; \
	fi
	@./scripts/release --tool "$(TOOL)" --version "$(VERSION)" $(if $(filter 1,$(APPLY)),--apply,)

clean:
	@find . -type d \( -name __pycache__ -o -name .pytest_cache \) -prune -exec rm -rf {} +
	@find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
	@echo "Clean."


package-distribution:
	@if [[ -z "$(OUT)" ]]; then echo "ERROR: OUT=<archive.zip> is required"; exit 2; fi
	@python3 scripts/package-distribution "$(OUT)"

distribution-smoke:
	@if [[ -z "$(ARCHIVE)" ]]; then echo "ERROR: ARCHIVE=<archive.zip> is required"; exit 2; fi
	@python3 scripts/distribution-smoke "$(ARCHIVE)"
