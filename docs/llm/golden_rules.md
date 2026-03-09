1. We prefer pure functions over complex code with side effects. Ideally packaged as reusable library modules.
2. Doc strings and type hints are mandatory in programming languages supporting them
3. No magic numbers. Use configuration / constants modules
4. Unit tests are generally required
5. All network / API functions need to attempt to gracefully recover with retires with exponential backoff (up to  MAX_RETRIES)
6. All caught errors must be shown to the user in the UI and logged
7. All errors discovered need to be fixed as priority, not postponed
8. We aim at keeping individual code files below 500 lines if feasible. Refactor early into separate modules if growth anticipated
9. All display sizing should be relative to allow a large variety of screen sizes/formats/DPI constellations. Do not needlessly waste screen real estate by hard coding display item sizes.
10. Anything displayed on the screen as output needs to be user selectable for copy & paste
11. we use uv for python libraries / environments. Do not use pip / venv directly 
11. If in doubt about imlementation, pause and ask the user. Ideally, propose the solutions you have been thinking about.