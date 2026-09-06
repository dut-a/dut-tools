.PHONY: help test artifact-check freeze-check install install-editable
help:
	@printf '%s\n' 'deploy-pack' '' 'Targets:' '  make test              Run tests' '  make artifact-check    Run DEPLOY-PACK-ARTIFACT-01 gate' '  make freeze-check      Run DEPLOY-PACK-FREEZE-01 gate' '  make install           Install for current user' '  make install-editable  Install editable copy'
test:
	python3 -m unittest discover -s tests -p 'test_*.py' -v
artifact-check:
	python3 scripts/check-artifact01.py
freeze-check:
	python3 scripts/check-freeze.py
install:
	python3 -m pip install --user .
install-editable:
	python3 -m pip install -e .
