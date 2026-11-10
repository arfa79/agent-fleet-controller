# Stage 1 real-agent collection preflight

Date: 2026-11-10

## Goal

Start the required collection of at least 30 sanitized runs from each of two
real agent workload classes, without fabricating model waits or using production
data.

## Host result

Two installed agent CLIs were checked without reading or exposing credential
contents:

- Codex CLI `0.160.0` reported that it was not logged in.
- Claude Code `2.1.207` reported an existing OAuth login. A no-tools,
  non-persistent, bounded request using an explicit Sonnet model initialized but
  produced no model response before the 90-second timeout. A second preflight
  reproduced the same result.

The Codex command and isolation options were checked against the
[official OpenAI CLI reference](https://developers.openai.com/codex/cli/reference/)
before the login preflight; no Codex model request was attempted without an
authenticated CLI session.

The new `afc collect` path was then exercised with a 60-run target and a
20-second per-run timeout. It stopped after the first coding-class preflight,
returned its documented blocked status, propagated timeout return code 124,
recorded zero completed runs and one failed run, and produced a valid AFC
failure trace. It did not attempt the remaining 59 runs.

No 30-run cohort was started after these preflights. Repeating a blocked request
would spend time or quota without producing valid evidence. No prompt, model
response, credential, or session identifier was retained in the repository.

## Interpretation

This host currently lacks a working, authorized real-agent execution path for
the collection. The result does not show that AFC works or fails for real-agent
wait distributions. It also does not authorize substituting scripted sleeps,
network silence, or an entire opaque CLI invocation as reclaimable model waits.

The repository now provides `afc collect` so collection can begin immediately
when an authorized agent command succeeds. The remaining external requirement
is a working agent/model account and two sanitized, versioned task sets. The
collector will fail fast on the first unsuccessful class preflight before
attempting the full 30-by-2 cohort.
