"""Does the brief still say the right thing to each discipline?

Three questions, no network and no API key:

  1. Is the software brief byte-identical to what it was before disciplines
     existed? Every quotation prepared until now is a software quotation and
     none of them may change by a character - so the briefs are hashed, and the
     hashes below were taken from `app.prompts` before it was edited.
  2. Does an accounting brief speak accounting, and does it stop asking for the
     software fields?
  3. Does every non-software brief name that discipline's own section headings,
     all of them, in the order the discipline declares them?

    cd backend
    .venv/Scripts/python.exe scripts/check_kind_prompt.py        # Windows
    .venv/bin/python scripts/check_kind_prompt.py                # macOS / Linux

Exit code 0 means the software path is untouched and every other one is wired.

To re-baseline after a *deliberate* change to the software brief: run with
--print-hashes and paste the result over BASELINE. Do that only when the change
to the software wording was the point of the commit, never to make this pass.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# `backend/` on the path so `app.*` resolves however this file is invoked.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# The em dash and the middle dot in the briefs do not survive the Windows
# console codepage. Reconfigure before anything prints.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):  # pragma: no cover - non-standard stream
        pass

from app import kinds, prompts  # noqa: E402
from app.gemini_service import _build_contents  # noqa: E402
from app.schemas import ProposalRequest  # noqa: E402


#: Taken from `app.prompts` before the kind branch was added to it.
BASELINE = {
    "plain": "431eebc007a7789fdc1c9c70a92dfd2437d8649e2e5b085a904c010b23be6a33",
    "full": "61f7133a2b6736d7cc074f865addc75a589e6fbc9feae1c0188e4462ebfbdaf9",
    "untaxed": "2ea5b44e4f2fabead81f8b1afb5cae4f0afc1fec4063c596e0637ac7cf72c1dc",
    "tiered": "fd15dd51aa261b4b0c26182d37ba66ab8d20e14f73eb705e1e8bf4dfa6ab26b4",
    "SYSTEM_INSTRUCTION": "d02525ae5aea31570a954d0a9cd4f168bfd86b466dd7cb5e3252aa8962883e10",
    "REVISION_SYSTEM_INSTRUCTION": "5f70fe890fc7df1715be15cce61aeeabe02f59d4c71cf7797dcc38152c7d1452",
}

CARD = "ROLE: Senior Engineer | RATE: 11,500 | UNIT: day\nROLE: QA Analyst | RATE: 6,750 | UNIT: day"

#: One request per shape the brief can take, so a change to any branch of it is
#: caught rather than only a change to the plain one. These must stay exactly as
#: they were when the hashes above were taken - editing a request here fails the
#: check as loudly as editing the prompt, and means nothing.
CASES = {
    "plain": dict(
        req=ProposalRequest(brief="A booking site for a dive shop."),
        kwargs=dict(image_count=0),
    ),
    "full": dict(
        req=ProposalRequest(
            brief="Rebuild the ledger portal.",
            currency="php",
            client_name="Acme",
            project_name="Ledger Portal",
            market_region="Metro Manila",
            budget_hint="under 500k",
            timeline_hint="live before Q4",
            target_total=480000.0,
            tax_mode="inclusive",
        ),
        kwargs=dict(
            image_count=2,
            rate_card_text=CARD,
            unit_basis_text="a working day is eight hours",
            payment_terms_text="50 on signature, 50 on go-live",
            contingency_hidden=True,
            ceiling=500000.0,
        ),
    ),
    "untaxed": dict(
        req=ProposalRequest(brief="Zero-rated export work.", tax_mode="none"),
        kwargs=dict(image_count=1),
    ),
    "tiered": dict(
        req=ProposalRequest(brief="Three levels please.", project_name="Portal"),
        kwargs=dict(
            image_count=0,
            tier=prompts.TierSpec(
                name="Standard",
                index=1,
                names=["Essential", "Standard", "Complete"],
                above_total=900000.0,
                above_name="Complete",
            ),
            ceiling=1000000.0,
        ),
    ),
}

#: What an accounting brief must say, and what it must not ask for.
WANTED = "ledger"
FORBIDDEN = ("tech stack", "API", "deployment")

failures: list[str] = []


def report(ok: bool, message: str) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {message}")
    if not ok:
        failures.append(message)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def brief_for(name: str, **overrides) -> str:
    """One case's brief. `kind` unset means the call site that existed before."""
    case = CASES[name]
    kwargs = dict(case["kwargs"], **overrides)
    count = kwargs.pop("image_count")
    return prompts.build_brief(case["req"], count, **kwargs)


def brief_for_kind(kind_id: str, case: str = "plain") -> str:
    return brief_for(case, kind=kind_id)


# --- 1. The software brief has not moved --------------------------------------

#: Everything that must resolve to software: the call that predates disciplines,
#: the id itself, an empty one, and anything unrecognised.
SOFTWARE_IDS = ("software", "", "   ", "SOFTWARE", "nonsense", "accountancy")


def check_software_unchanged() -> None:
    print("1. the software brief is what it always was")

    for name in CASES:
        expected = BASELINE[name]
        report(digest(brief_for(name)) == expected, f"{name}: no kind passed at all")
        for kind_id in SOFTWARE_IDS:
            report(
                digest(brief_for(name, kind=kind_id)) == expected,
                f"{name}: kind={kind_id!r} resolves to the same bytes",
            )

    for name in ("SYSTEM_INSTRUCTION", "REVISION_SYSTEM_INSTRUCTION"):
        report(digest(getattr(prompts, name)) == BASELINE[name], f"{name} is unchanged")

    # main.py hands `generate_estimate` eight positional arguments and cannot be
    # edited in this step, so the new parameter has to be the last one. Calling
    # the same way here is what proves it: bound one place earlier, `kind` would
    # take the ceiling and every one of these hashes would still pass.
    positional = _build_contents(
        CASES["plain"]["req"], [], "", "", "", False, None, 0.0
    )
    report(
        digest(positional[0]) == BASELINE["plain"],
        "_build_contents called positionally, as main.py calls it, still builds it",
    )


# --- 2. An accounting brief speaks accounting ---------------------------------


def check_accounting_vocabulary() -> None:
    print("2. an accounting brief speaks accounting")

    kind = kinds.resolve("accounting")
    brief = brief_for_kind("accounting")
    report(WANTED in brief, f"the brief contains {WANTED!r}")

    # The forbidden words are checked against what PRISM writes, not against the
    # guidance quoted from app/kinds.py - which contains all three, inside its
    # own prohibition: "Never write about a tech stack, an API, a deployment".
    # A check that fails on the sentence forbidding the thing is not a check.
    authored = brief.replace(kind.guidance, "")
    for token in FORBIDDEN:
        report(token not in authored, f"PRISM's own words never say {token!r}")

    print("     - and for the record, in the whole brief including that prohibition:")
    start = brief.find(kind.guidance)
    end = start + len(kind.guidance)
    for token in FORBIDDEN:
        offsets = []
        index = brief.find(token)
        while index >= 0:
            offsets.append(index)
            index = brief.find(token, index + 1)
        where = ", ".join(str(offset) for offset in offsets) or "nowhere"
        inside = bool(offsets) and all(start <= offset < end for offset in offsets)
        print(
            f"       {token!r}: at {where}"
            + (f" - all within the guidance, chars {start}-{end}" if inside else "")
        )


# --- 3. Every discipline gets its own headings --------------------------------


def check_headings_named() -> None:
    print("3. every non-software brief names its own section headings, in order")

    # Both the bare brief and the one with every optional block turned on: the
    # discipline is appended last, and `full` is the case that would catch it if
    # anything ever gets added after it.
    for case in ("plain", "full"):
        for kind in kinds.KINDS:
            if kind.id == kinds.DEFAULT.id:
                continue
            brief = brief_for_kind(kind.id, case)
            positions = [brief.find(section.heading) for section in kind.sections]
            missing = [
                section.heading
                for section, index in zip(kind.sections, positions)
                if index < 0
            ]
            report(
                not missing,
                f"{case}/{kind.id}: all {len(kind.sections)} headings present"
                + (f" - missing {missing}" if missing else ""),
            )
            report(
                positions == sorted(positions),
                f"{case}/{kind.id}: headings appear in the declared order",
            )
            report(
                f"exactly {len(kind.sections)} entries" in brief
                and "developer.sections" in brief,
                f"{case}/{kind.id}: the brief asks for {len(kind.sections)} entries "
                f"in developer.sections",
            )
            report(
                kind.guidance in brief,
                f"{case}/{kind.id}: its guidance is carried through verbatim",
            )


def print_hashes() -> None:
    fresh = {name: digest(brief_for(name)) for name in CASES}
    for name in ("SYSTEM_INSTRUCTION", "REVISION_SYSTEM_INSTRUCTION"):
        fresh[name] = digest(getattr(prompts, name))
    print(json.dumps(fresh, indent=4))


def main() -> int:
    if "--print-hashes" in sys.argv:
        print_hashes()
        return 0

    check_software_unchanged()
    print()
    check_accounting_vocabulary()
    print()
    check_headings_named()
    print()

    if failures:
        print(f"{len(failures)} FAILED:")
        for message in failures:
            print(f"  - {message}")
        return 1

    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
