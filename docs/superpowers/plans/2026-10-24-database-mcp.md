# MySQL / PostgreSQL MCP

User authorized direct development. Add independently deployable remote HTTP MCP services; never import developer environment MCPs or add stdio transport.

- [x] Tests: single SELECT validation, write/transaction rejection, execution limits, output truncation, metadata binding and HTTP authentication.
- [x] Shared database adapter plus engine-specific connection/read-only transaction handling.
- [x] MCP endpoints: list_tables, describe_table, query; independent environment-based tokens and credentials.
- [x] Windows launch script, standalone Dockerfile and Chinese setup instructions.
- [x] Run full Python tests and a real MCP-over-HTTP protocol test. Actual database verification depends on available database services/credentials.

Verification: full suite 27 passed; 12 database-specific tests include both engines over actual HTTP MCP with a test-double database, driver read-only/cleanup contracts and SQL guards. CLI help and pip check succeeded. Independent read-only review found no important issues. No live DB credentials or Docker executable were available; no persistent database MCP service has been started or registered.
