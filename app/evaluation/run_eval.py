"""Live-evaluation command for the drafting-comparison harness.

Defaults to OFFLINE MOCK mode -- running this script with no flags never
makes a real (paid) model call. Real calls require explicitly passing
`--mode live` AND an explicit `--max-calls` budget; the harness stops once
that many calls have been made, never silently exceeding it.

Usage:
    # offline, free, deterministic -- verifies the harness only
    python -m evaluation.run_eval

    # a real, budgeted comparison run (requires OPENAI_API_KEY in .env)
    python -m evaluation.run_eval --mode live --max-calls 10

Results are written to the EvalRun/EvalResult tables in whatever database
DATABASE_URL points at (see database.py) -- separate tables from `leads`
and `qualification_labels`, so this is safe to point at the dev database,
but you can also point DATABASE_URL at an isolated sqlite file first if you
prefer not to mix eval history with anything else.
"""
import argparse
import sys

import database
from evaluation.dataset import load_dataset
from evaluation import harness, store


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["mock", "live"], default="mock", help="mock (default, free, offline) or live (real, budgeted, paid model calls)")
    parser.add_argument("--max-calls", type=int, default=None, help="required for --mode live: hard cap on real model calls made")
    parser.add_argument("--dataset", default="companies_v1", help="fixture dataset name under evaluation/fixtures/")
    parser.add_argument("--language", default="en")
    parser.add_argument("--tone", default="professional")
    parser.add_argument("--length", default="medium", choices=["short", "medium", "long"])
    parser.add_argument("--case-ids", default=None, help="comma-separated case ids to run (default: all cases in the dataset)")
    parser.add_argument("--notes", default="")
    args = parser.parse_args(argv)

    if args.mode == "live" and not args.max_calls:
        parser.error("--mode live requires --max-calls (an explicit budget on real model calls).")

    dataset = load_dataset(args.dataset)
    case_ids = args.case_ids.split(",") if args.case_ids else None

    print(f"Running evaluation: mode={args.mode} dataset={dataset.version} cases={len(case_ids) if case_ids else len(dataset.cases)} " f"{'max_calls=' + str(args.max_calls) if args.mode == 'live' else '(mock -- no real calls)'}")

    outcomes = harness.run(
        dataset, mode=args.mode, language=args.language, tone=args.tone, length=args.length,
        max_calls=args.max_calls, case_ids=case_ids,
    )

    db = database.SessionLocal()
    try:
        run = store.save_run(
            db, dataset, outcomes, mode=args.mode, language=args.language, tone=args.tone, length=args.length,
            max_calls=args.max_calls, notes=args.notes,
        )
    finally:
        db.close()

    errors = sum(1 for _, o in outcomes if o.error)
    print(f"Saved EvalRun id={run.id} with {len(outcomes)} results ({errors} error(s)).")
    if args.mode == "mock":
        print("NOTE: this was a MOCK run -- it verifies the harness only and does not measure real model quality.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
