# Tasks

Complete every task below. Keep changes minimal and scoped to what each task
asks for. Run `python3 -m unittest discover -s tests -v` while developing.

- [ ] Fix `subtract` in `calculator.py`: it currently returns `b - a` instead of `a - b`.
- [ ] Implement `multiply` in `calculator.py`: return the product of `a` and `b`.
- [ ] Implement `divide` in `calculator.py`: return `a / b`, and raise `ValueError("cannot divide by zero")` when `b` is zero.
- [ ] Add input validation: every operation (`add`, `subtract`, `multiply`, `divide`) must raise `ValueError` when either argument is a boolean.
- [ ] Update `README.md`: add a `## Usage` section that documents all four operations with one example each.
