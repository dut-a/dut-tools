# DEPLOY-PACK-UNFREEZE-01

DEPLOY-PACK-FREEZE-01 froze deploy-pack 1.9.1 and required explicit unfreeze for architecture expansion.

DEPLOY-PACK-ARTIFACT-01 is that explicit, bounded expansion. It adds one semantically separate Git-independent packaging mode for already-built deployment directories. Existing Git-driven packaging, evidence, recovery, trust, and assurance behavior remains compatibility-sensitive and is not reopened by this increment.

Release line: **1.10.0**.

Scope reopened only for `deploy-pack artifact`, deterministic ZIP/TAR.GZ artifact-directory writing, canonical artifact-path validation, generic required-path assertions, and associated tests/documentation.

The 1.9.1 freeze documentation and evidence remain historical records and are not rewritten.
