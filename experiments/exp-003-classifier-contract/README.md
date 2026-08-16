# exp-003 — classifier contract

This experiment is a declarative example. It is not a recorded model result.

The matrix fixes the model and the thinking level. It repeats each cell three times. The matrix changes only the agent instruction file.

The task supplies one evidence bundle to the agent. The task keeps the expected label in a separate file. The harness does not add that label to the agent prompt.

`judging.inputs` is `[output]`, so the only per-candidate material in the judge prompt is each candidate's own output. The judge prompt also carries material that is the same for both candidates: the rubric, the shared task prompt, and the evidence bundle from the task input. The judge needs the evidence to check whether a cited identifier is real and whether the decision follows from the text.

The judge never sees the hidden expected label, and never sees the evaluator summary. The evaluator summary is calculated from that label, so passing it to the judge would hand over the answer key and turn the judge score into a restatement of the deterministic score. The harness refuses to load a `transform-classifier` experiment that names `evaluator_summary` in `judging.inputs`.

The deterministic evaluator requires `classifier-result/v1`. It calculates accuracy, precision, recall, F1, and Brier score. It records invalid JSON or an invalid schema as a benchmark failure with `schema_validity=0`.

The schema validity rate counts only cells that produced a model output to hold against the contract. A model output that is not valid JSON, or that does not satisfy `classifier-result/v1`, counts in the rate as a failure. A cell that failed for a harness reason (`infrastructure`) or whose agent never completed (`agent_execution`) is excluded from the rate entirely, in both the numerator and the denominator, and is reported separately as an excluded cell. The model is charged only for its own answer.

Do not record a frozen baseline until these conditions are true:

- A human reviewer signs the static fixture labels.
- The run uses a pinned container digest.
- The run records the intended and observed model versions.
- Every cell completes three repetitions.

No baseline result is committed because no qualifying run occurred.
