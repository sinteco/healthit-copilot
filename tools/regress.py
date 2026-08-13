#!/usr/bin/env python3
"""Bulk HL7-to-FHIR regression harness.

Replays a folder of HL7 v2 messages (*.hl7 / *.txt) through
hl7_to_fhir_skeleton and compares the resulting Bundles against a baseline:

  # First run: record the baseline
  python3 tools/regress.py samples/ --baseline baselines/

  # Later runs (e.g. after changing the mapper): diff against it
  python3 tools/regress.py samples/ --baseline baselines/

Exit codes: 0 = all match (or baseline created), 1 = drift detected,
2 = usage/input error. New messages without a baseline are recorded and
reported as NEW. Use --update to overwrite drifted baselines intentionally.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))
import server  # noqa: E402


def _diff_paths(a, b, path="$"):
    """Yield human-readable differences between two JSON values."""
    if type(a) is not type(b):
        yield f"{path}: type {type(a).__name__} -> {type(b).__name__}"
        return
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                yield f"{path}.{k}: added"
            elif k not in b:
                yield f"{path}.{k}: removed"
            else:
                yield from _diff_paths(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        if len(a) != len(b):
            yield f"{path}: length {len(a)} -> {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            yield from _diff_paths(x, y, f"{path}[{i}]")
    elif a != b:
        yield f"{path}: {json.dumps(a)} -> {json.dumps(b)}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("messages_dir", help="folder of *.hl7 / *.txt messages")
    ap.add_argument("--baseline", required=True,
                    help="folder holding expected Bundle JSON per message")
    ap.add_argument("--update", action="store_true",
                    help="overwrite drifted baselines instead of failing")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.messages_dir):
        print(f"error: not a directory: {args.messages_dir}", file=sys.stderr)
        return 2
    os.makedirs(args.baseline, exist_ok=True)

    names = sorted(n for n in os.listdir(args.messages_dir)
                   if n.lower().endswith((".hl7", ".txt")))
    if not names:
        print(f"error: no .hl7/.txt files in {args.messages_dir}",
              file=sys.stderr)
        return 2

    drifted, new, matched, failed = [], [], [], []
    for name in names:
        with open(os.path.join(args.messages_dir, name)) as fh:
            msg = fh.read()
        bundle = server.hl7_to_fhir_skeleton(msg)
        if "error" in bundle and "resourceType" not in bundle:
            failed.append((name, bundle["error"]))
            continue
        base_path = os.path.join(args.baseline,
                                 os.path.splitext(name)[0] + ".json")
        if not os.path.exists(base_path):
            with open(base_path, "w") as fh:
                json.dump(bundle, fh, indent=2, sort_keys=True)
            new.append(name)
            continue
        with open(base_path) as fh:
            expected = json.load(fh)
        diffs = list(_diff_paths(expected, bundle))
        if diffs:
            drifted.append((name, diffs))
            if args.update:
                with open(base_path, "w") as fh:
                    json.dump(bundle, fh, indent=2, sort_keys=True)
        else:
            matched.append(name)

    for name in matched:
        print(f"OK       {name}")
    for name in new:
        print(f"NEW      {name} (baseline recorded)")
    for name, err in failed:
        print(f"FAILED   {name}: {err}")
    for name, diffs in drifted:
        tag = "UPDATED " if args.update else "DRIFT   "
        print(f"{tag} {name}:")
        for d in diffs[:20]:
            print(f"    {d}")
        if len(diffs) > 20:
            print(f"    ... and {len(diffs) - 20} more")

    print(f"\n{len(matched)} ok, {len(new)} new, {len(drifted)} drifted, "
          f"{len(failed)} failed")
    if failed:
        return 1
    if drifted and not args.update:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
