# Security policy for the prototype

Agent Fleet Controller is pre-alpha research software and is not approved for
production operation.

## Data policy

Only sanitized lifecycle metadata may be used. Never add prompts, responses,
tool arguments, credentials, source code, personal data, customer identifiers,
or production traces to this repository.

## Execution policy

Future live adapters must be observe-only by default, restrict actions to
explicitly enrolled workloads, use least-privileged identities, and record
sanitized decisions. A configuration change must not silently enable production
mutation.

Report suspected vulnerabilities privately to the repository owner once a
private reporting channel has been established. Do not place exploit details or
sensitive environment data in a public issue.
