# Reproducible experiment manifests

`afc experiment` validates one strict JSON manifest and runs workload-class
analysis with the pinned policy:

```bash
afc experiment examples/experiment-manifest.json --pretty
```

Relative collection inputs resolve from the manifest directory. Unknown and
missing fields are rejected so silent schema drift cannot change the meaning of
a published result.

Schema version 1 requires:

- a safe experiment ID and evidence kind: `synthetic`, `controlled_local`, or
  `real_workload`;
- runtime name, version, and driver;
- agent name/version, model provider/model ID, and tool-environment revision;
- workload dataset ID, revision, and an explicit real-workload boolean;
- an `always_on` or `native_runtime_policy` baseline, matching native-policy
  boolean, and price-table revision;
- evaluator name and version;
- trace inputs and minimum collection gates; and
- every `SimulationPolicy` value, including costs and acceptance thresholds.

CPU and memory allocations remain authoritative in each lifecycle trace because
different runs may have different sandbox sizes. The manifest records experiment
provenance and configuration; it does not certify that its human-provided labels
are true. Preserve external workload and evaluator records in the experiment
report without putting prompts, responses, credentials, customer names, or
source code into AFC traces or manifests.

An experiment report still distinguishes modeled allocation savings from
best-effort local process observations. It cannot establish native sandbox
resource release until an always-on and native-runtime baseline are measured on
the same runtime and workload.
