# Future PostgreSQL integration tests

No integration tests are implemented in Milestone 1A. The default test command
collects only `tests/unit/` and requires no database or Docker service.

Before adding integration tests, establish an explicit opt-in command, dedicated
test database configuration, migrations, and safeguards against connecting to or
resetting a non-test database. Use real PostgreSQL for persistence semantics.
External weather requests remain mocked. The offline network guard will need a
scoped exception for the approved test database, not unrestricted network access.
