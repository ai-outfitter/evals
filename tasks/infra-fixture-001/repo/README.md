# Calculator fixture

Repair `normalize_and_sum` in `calculator.py`.

Requirements:

- Accept an iterable containing integers, floats, and numeric strings.
- Trim surrounding whitespace from numeric strings.
- Convert decimal numeric strings to numbers before summing.
- Reject booleans, blank strings, and unsupported values with `ValueError`.
- Do not mutate the input.

Run `python3 -m unittest discover -s tests -v` while developing. The evaluation harness may apply additional tests.
