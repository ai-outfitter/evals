# exp-003 — classifier contract

This experiment is a declarative example. It is not a recorded model result.

The matrix fixes the model and the thinking level. It repeats each cell three times. The matrix changes only the agent instruction file.

The task supplies one evidence bundle to the agent. The task keeps the expected label in a separate file. The harness does not add that label to the agent prompt.

The deterministic evaluator requires `classifier-result/v1`. It calculates accuracy, precision, recall, F1, and Brier score. It records invalid JSON or an invalid schema as a benchmark failure with `schema_validity=0`.

Do not record a frozen baseline until these conditions are true:

- A human reviewer signs the static fixture labels.
- The run uses a pinned container digest.
- The run records the intended and observed model versions.
- Every cell completes three repetitions.

No baseline result is committed because no qualifying run occurred.
