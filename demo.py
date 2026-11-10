"""Generate a real, model-free fixture report using the public supervisor APIs."""

import argparse
from pathlib import Path
import tempfile
from typing import Sequence

from .policy import Policy
from .provenance import Provenance
from .records import Action, Grant
from .report import generate
from .supervisor import Supervisor


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="planner-demo-") as directory:
        base = Path(directory)
        root = base / "project"
        root.mkdir()
        (root / "README.md").write_text("A disposable documentation fixture.\n")
        (root / ".env").write_text("DUMMY_SECRET_NOT_FOR_PERSISTENCE\n")
        supervisor = Supervisor(base / "control.sqlite")
        provenance = Provenance(base / "provenance.sqlite")
        try:
            task = supervisor.propose(Policy(root, read=("**",), write=("src/**",), deny_read=(".env*",)))
            supervisor.approve_task(task, revision=1)
            supervisor.approve_worker(Grant(task, "fixture", ("**",), ("src/**",)))
            supervisor.authorize(Action(task, "fixture", "read", ".env"))
            allowed = supervisor.authorize(Action(task, "fixture", "read", "README.md"))
            if not allowed.allowed:
                raise RuntimeError("Fixture read was unexpectedly denied")
            # This is an actual trusted fixture read, not a model-input observation.
            (root / "README.md").read_text()
            provenance.register_task(task, str(root.resolve()))
            observed = provenance.record_input(task, "fixture", "fixture_read", "README.md")
            provenance.add_note(str(root.resolve()), task, "fixture",
                                "Disposable fixture observation; no model was called.", [observed.id])
            request = supervisor.request_exception(Action(task, "fixture", "write", "README.md"))
            supervisor.approve_exception(request)
            supervisor.authorize(Action(task, "fixture", "write", "README.md"))
            data = supervisor.export(task)
            data["provenance"] = provenance.export(task)
            generate(data, args.output)
        finally:
            provenance.close()
            supervisor.close()
    print(args.output.resolve())


if __name__ == "__main__":
    main()
