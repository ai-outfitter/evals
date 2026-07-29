# cycle fixture repository

Throwaway target repository for the issue → PR → review → merge
infrastructure fixture (`task-002`). Agents interact with it only through
the local forge shim (`fixtures/task-002/forge/forge.py`) and git.

The default branch is `main` and is protected: direct pushes are rejected
by a pre-receive hook; changes land only through a forge auto-merge that
requires one current approval and no outstanding request-changes review.
