# Markdown planning inventory (#32)

Run before fixing migration preservation policy and block limits. This tool does not
convert, validate block documents, change schema, or certify migration readiness.
It emits aggregate counts and per-document maxima, never content, row IDs, titles,
URLs, credentials, or parser/driver exception strings. Repeated snapshots count as
separate retained documents. `non_string_content_rows` requires investigation.
`estimated_ids` is a planning estimate, not a canonical converter result; token depth
is not block-document depth. HTML/possible MDX and incompatible list/quote/control
shapes require preservation decisions before migration. Missing counters mean zero.

Use a reviewed checkout and a Python image with the repository's locked dependencies.
For isolated seed inventory (does not describe user/live data):

```sh
docker run --rm --network none --read-only --tmpfs /tmp \
  -v "$PWD/backend:/source:ro" -w /source -e PYTHONPATH=/source \
  --entrypoint python <reviewed-image> -B -m ops.post_markdown_inventory --legacy
```

PM executes the live variant with `--database`, the existing approved DB network,
`DATABASE_URL_FILE` pointing to a read-only mounted connection secret, and the live
`APP_BASE_PATH`. Do not put database URLs/passwords on the command line. No API
restart, writer stop, migration, upload, or provider request is needed. Use the
reviewed source mount/workdir/PYTHONPATH; verify its revision before execution.

Database mode starts one REPEATABLE READ READ ONLY transaction, with statement
60s and lock 5s timeouts, then streams only content from all rows of
`research.post_versions`, `post_drafts`, `post_proposals`, `post_publications`.
It includes archived/withdrawn/accepted/rejected states with no filtering. It does
not fetch author/owner identifiers or modify any rows. It makes four SELECTs and
transaction-local SETs. Output is released only after all four scans succeed.
A failure exits 1 with a generic message; do not enable SQL echo or raw traceback
logging when diagnosing production. The consistent snapshot is as of execution;
new writes afterwards still require migration preflight.

Report the output's counts/maxima in #32 and return it to backend. Source seed data
alone cannot establish that user content is safe to migrate. Review the live result
before contract v0.3, schema fixtures, or any data migration policy is finalized.
