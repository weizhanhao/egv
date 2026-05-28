#!/usr/bin/env python3
"""egv_cli.py — Lifecycle command dispatcher.

Usage:
  python3 egv_cli.py brainstorm --project-root <path> --idea "..."
  python3 egv_cli.py requirements --project-root <path> --path <file> | --text "..."
  python3 egv_cli.py design-review --project-root <path> --path <file> | --text "..."
  python3 egv_cli.py reflect --project-root <path>
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lifecycle import log_brainstorm, log_requirements, log_design_review, log_reflection


def _read_text(args) -> str:
    if getattr(args, "text", None):
        return args.text
    if getattr(args, "path", None):
        return Path(args.path).read_text()
    print("Provide either --text or --path", file=sys.stderr)
    sys.exit(2)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("brainstorm")
    pb.add_argument("--project-root", required=True)
    pb.add_argument("--idea", required=True)

    pr = sub.add_parser("requirements")
    pr.add_argument("--project-root", required=True)
    pr.add_argument("--path")
    pr.add_argument("--text")

    pd = sub.add_parser("design-review")
    pd.add_argument("--project-root", required=True)
    pd.add_argument("--path")
    pd.add_argument("--text")

    pf = sub.add_parser("reflect")
    pf.add_argument("--project-root", required=True)

    pl = sub.add_parser("learn")
    pl.add_argument("--project-root", required=True)
    pl.add_argument("--mode", choices=["propose", "list-pending", "approve"], default="propose")
    pl.add_argument("--pattern-id", help="Required for --mode approve")

    ply2 = sub.add_parser("layer2")
    ply2.add_argument("--project-root", required=True)
    ply2.add_argument("--blast-radius", required=True, help="Path to blast-radius.json from a prior run")

    pl2r = sub.add_parser("layer2-review")
    pl2r.add_argument("--project-root", required=True)
    pl2r.add_argument("--mode", choices=["list", "approve", "reject"], default="list")
    pl2r.add_argument("--proposal-id", help="Required for approve/reject")

    args = p.parse_args()
    project_root = Path(args.project_root).resolve()

    if args.cmd == "brainstorm":
        entry = log_brainstorm(project_root, args.idea)
    elif args.cmd == "requirements":
        entry = log_requirements(project_root, _read_text(args), source=getattr(args, "path", None))
    elif args.cmd == "design-review":
        entry = log_design_review(project_root, _read_text(args), source=getattr(args, "path", None))
    elif args.cmd == "reflect":
        entry = log_reflection(project_root)
    elif args.cmd == "learn":
        from learning import propose_patterns, list_pending_patterns, approve_pattern
        if args.mode == "propose":
            entry = propose_patterns(project_root)
        elif args.mode == "list-pending":
            entry = {"pending": list_pending_patterns(project_root)}
        elif args.mode == "approve":
            if not args.pattern_id:
                print("--pattern-id required for --mode approve", file=sys.stderr)
                sys.exit(2)
            entry = approve_pattern(project_root, args.pattern_id)
            if entry is None:
                entry = {"error": f"Pattern {args.pattern_id} not found or already approved"}
    elif args.cmd == "layer2":
        from layer2 import synthesize_proposals
        entry = synthesize_proposals(Path(args.project_root), Path(args.blast_radius))
    elif args.cmd == "layer2-review":
        from model_keeper import keeper_path_for_project_root, load_model_keeper, save_model_keeper
        keeper_path = keeper_path_for_project_root(project_root)
        keeper = load_model_keeper(keeper_path)
        proposals = keeper.get("lifecycle_artifacts", {}).get("synthesis_proposals", [])
        if args.mode == "list":
            pending = [p for p in proposals if not p.get("human_reviewed", False)]
            entry = {"pending_count": len(pending), "pending": pending}
        elif args.mode in ("approve", "reject"):
            if not args.proposal_id:
                print("--proposal-id required", file=sys.stderr)
                sys.exit(2)
            target = None
            for prop in proposals:
                if prop.get("proposal_id") == args.proposal_id:
                    prop["human_reviewed"] = True
                    prop["review_decision"] = args.mode
                    target = prop
                    break
            if target is None:
                entry = {"error": f"proposal {args.proposal_id} not found"}
            else:
                save_model_keeper(keeper_path, keeper)
                entry = target
    else:
        sys.exit(2)

    print(json.dumps(entry, indent=2))


if __name__ == "__main__":
    main()
