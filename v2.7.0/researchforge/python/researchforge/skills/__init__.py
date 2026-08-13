"""Skill implementations.

Importing this package registers every implementation. A skill that is specified
but not built is present here as a stub that raises — never as a missing entry
that would let the orchestrator skip a stage silently.

Module boundaries follow the pipeline, not the file system's convenience: each
module owns one plane of the architecture and nothing reaches across.
"""
from . import intake          # noqa: F401  repo, sandbox, ingestion, paper model
from . import evidence        # noqa: F401  providers, coverage, search, citations, claim graph
from . import reproduction    # noqa: F401  RL grading and the degradation path
from . import innovation      # noqa: F401  seeds, portfolio, evaluation, ranking, human gate
from . import planning        # noqa: F401  blueprint, experiment specs, evaluator isolation
from . import execution       # noqa: F401  scaffolding, repair, branch search, ledger
from . import analysis        # noqa: F401  data prep/analysis, statistics, findings
from . import writing         # noqa: F401  manuscript, claim/citation audit, review
from . import artifacts_out   # noqa: F401  figures, deck, release gate
from . import meta            # noqa: F401  benchmark, eval harness, skill evolution
from . import stubs           # noqa: F401  orchestrator state persistence; anything still unbuilt
