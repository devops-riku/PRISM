"""Proposals built from a quotation.

A quotation is what the studio priced; a proposal is the document the client
signs. This router builds the second from the first - one bundle in, one
written-up document out - and then serves it as markdown, as a print view and
as a PDF.

The word "proposal" is overloaded here for historical reasons, and the seam
runs straight through this file. Everywhere else in this API "proposal" means
the client half of a quotation, down to the filename helper that turns it into
"-quotation.md"; the documents this router builds are a different thing that
happens to share the noun. Where the two would collide the generated file is
called "proposal-document" instead.

Everything a proposal was built from is snapshotted into it - terms, template,
look - because a document that has been sent must say in April what it said in
March.
"""

from __future__ import annotations

import asyncio
from typing import List

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, Response

from app.features.documents.application import service as documents
from app.features.documents.domain import design, policies, template
from app.features.jobs.application import service as jobs
from app.features.notifications.infrastructure import inbox
from app.features.quotations.domain.models import (
    Estimate,
    GeneratedFile,
    PolicyRecord,
    ProposalDocument,
    ProposalDocumentSummary,
    SectionRecord,
)
from app.features.quotations.infrastructure import repository as storage
from app.features.quotations.infrastructure.gemini import (
    GeminiConfigError,
    GeminiResponseError,
    generate_proposal,
)
from app.features.rendering.presentation import (
    render_pdf,
    render_print_html,
    render_proposal,
)
from app.features.workspaces.application import settings
from app.features.workspaces.infrastructure import reference
from app.shared.presentation.http import deps

router = APIRouter()


def _document_files(document: "ProposalDocument", markdown: str) -> List[GeneratedFile]:
    base = f"/api/documents/{document.id}/files"
    stem = deps.slugify(document.project_name or document.id, "proposal")
    return [
        GeneratedFile(
            # Not "proposal": that token already means "the client half of a
            # quotation" throughout this API, down to the filename helper that
            # turns it into "-quotation.md".
            kind="proposal-document",
            filename=f"{stem}-proposal.md",
            markdown=markdown,
            download_url=f"{base}/proposal.md",
            print_url=f"{base}/proposal.html",
            pdf_url=f"{base}/proposal.pdf",
        )
    ]


def _summarise_document(document: ProposalDocument) -> ProposalDocumentSummary:
    return ProposalDocumentSummary(
        id=document.id,
        created_at=document.created_at,
        quotation_id=document.quotation_id,
        quotation_ref=document.quotation_ref,
        reference=document.reference,
        title=document.title,
        client_name=document.client_name,
        project_name=document.project_name,
        currency=document.currency,
        total=document.total,
        policy_count=len(document.policies),
    )


def _require_document(document_id: str) -> ProposalDocument:
    document = documents.get(document_id)
    if document is None:
        raise HTTPException(
            status_code=404,
            detail=f"No proposal with the reference {document_id!r}.",
        )
    return document


def _document_markdown(document: ProposalDocument) -> str:
    """The proposal's markdown, re-rendered from its own snapshot if need be."""
    for generated in document.files:
        if generated.kind == "proposal-document" and generated.markdown.strip():
            return generated.markdown

    bundle = storage.get(document.quotation_id)
    if bundle is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This proposal has no stored markdown and the quotation it was built from is "
                "no longer on file, so it cannot be re-rendered. Build it again."
            ),
        )
    return render_proposal(document, bundle.estimate)


def _document_estimate(document: ProposalDocument) -> Estimate:
    """The quotation this was built from, or an empty one if it has been deleted.

    The markdown is already written and carries every figure, so a missing
    quotation costs the print view its letterhead detail rather than the
    document itself.
    """
    bundle = storage.get(document.quotation_id)
    return bundle.estimate if bundle is not None else Estimate()


@router.post("/api/documents", response_model=jobs.JobView, status_code=202, tags=["documents"])
async def build_document(quotation_id: str = Form(...)) -> jobs.JobView:
    """Build a proposal from one quotation, exactly as that quotation stands.

    Tiers are not merged and revisions are not followed: whichever bundle id is
    sent is the one written up. A studio selling the Standard tier sends
    Standard. Rebuilding produces a new document with its own id rather than
    editing this one - a proposal that has been sent must not change afterwards.

    Answers with a job, like every other Gemini pass in this API.
    """
    bundle = deps.require_bundle(quotation_id)
    studio = settings.load()
    clauses = policies.resolve(studio.policies)
    sections = template.resolve(studio.proposal_sections)
    look = design.resolve(studio.proposal_design)

    job = jobs.create(
        kind="proposal",
        title=f"Proposal for {bundle.estimate.project_name or bundle.id}",
        detail=" \u00b7 ".join(
            part
            for part in (
                bundle.estimate.client_name,
                bundle.estimate.quotation_ref,
                f"{len(clauses)} clauses",
            )
            if part
        ),
        steps=["Writing the proposal", "Assembling the document"],
    )

    async def run() -> None:
        jobs.start(job.id, "Reading the quotation")
        try:
            narrative = await generate_proposal(
                bundle.estimate,
                studio.studio_name,
                [clause.title for clause in clauses],
            )
            jobs.step(job.id, 0, "Assembling the document")

            document = ProposalDocument(
                id=storage.new_id(),
                created_at=storage.utc_now_iso(),
                quotation_id=bundle.id,
                quotation_ref=bundle.estimate.quotation_ref,
                # Taken from the studio's proposal series the moment the
                # document exists. A number recomputed at render time would
                # change every time somebody opened the thing they had sent.
                reference=reference.build(
                    studio.proposal_prefix,
                    studio.proposal_reference_mode,
                    series=reference.PROPOSALS,
                ),
                quotation_issued_at=bundle.created_at,
                title=narrative.title or bundle.estimate.project_name,
                client_name=bundle.estimate.client_name,
                project_name=bundle.estimate.project_name,
                currency=bundle.estimate.currency,
                total=bundle.estimate.cost.total,
                studio_name=studio.studio_name,
                signatory=studio.proposal_signatory,
                signatory_title=studio.proposal_signatory_title,
                narrative=narrative,
                # A snapshot, not a reference. A proposal sent in March says
                # what it said in March, whatever Settings looks like in April.
                policies=[
                    PolicyRecord(id=clause.id, title=clause.title, body=clause.body)
                    for clause in clauses
                ],
                # The template is snapshotted for the same reason the terms are:
                # a document that has been sent keeps the shape it was sent in.
                sections=[
                    SectionRecord(id=section.id, heading=section.heading)
                    for section in sections
                ],
                # And the look, for the third time the same reason: a studio
                # that rebrands in April has not restyled what it sent in March.
                design=look,
            )
            document.files = _document_files(
                document, render_proposal(document, bundle.estimate)
            )
            documents.save(document)
            jobs.step(job.id, 1, "Ready")
            jobs.finish(job.id, [document.id])
            inbox.notify(
                "proposal_ready",
                inbox.ACTOR,
                {
                    "title": "Proposal ready to send",
                    "body": " · ".join(
                        part
                        for part in (
                            document.title or document.project_name,
                            document.client_name,
                            f"{len(document.policies)} clauses",
                        )
                        if part
                    ),
                    "href": f"#/p/{document.id}",
                },
            )
        except GeminiConfigError as exc:
            deps.logger.error("Proposal blocked by configuration: %s", exc)
            jobs.fail(job.id, str(exc))
        except GeminiResponseError as exc:
            deps.logger.error("Unusable proposal response: %s | snippet=%s", exc, exc.snippet)
            jobs.fail(job.id, str(exc))
            inbox.notify(
                "proposal_failed",
                inbox.ACTOR,
                {
                    "title": "That proposal was not built",
                    "body": f"{exc} The quotation behind it is unchanged.",
                    "href": "#/proposals",
                },
            )
        except Exception:  # pragma: no cover - genuinely unexpected
            deps.logger.exception("Unhandled failure while building a proposal")
            jobs.fail(job.id, "The proposal could not be built. The error is in the API log.")

    task = asyncio.create_task(run())
    deps.BACKGROUND.add(task)
    task.add_done_callback(deps.BACKGROUND.discard)

    return jobs.view(job)


@router.get("/api/documents", response_model=List[ProposalDocumentSummary], tags=["documents"])
async def list_documents(limit: int = 100) -> List[ProposalDocumentSummary]:
    """Every proposal built, newest first."""
    return [_summarise_document(document) for document in documents.listing(limit)]


@router.get("/api/documents/{document_id}", response_model=ProposalDocument, tags=["documents"])
async def read_document(document_id: str) -> ProposalDocument:
    return _require_document(document_id)


@router.delete("/api/documents/{document_id}", status_code=204, tags=["documents"])
async def delete_document(document_id: str) -> Response:
    if not documents.delete(document_id):
        raise HTTPException(
            status_code=404, detail=f"No proposal with the reference {document_id!r}."
        )
    return Response(status_code=204)


@router.get("/api/documents/{document_id}/files/proposal.md", tags=["documents"])
async def download_document_markdown(document_id: str) -> Response:
    document = _require_document(document_id)
    stem = deps.slugify(document.project_name or document.id, "proposal")
    return Response(
        content=_document_markdown(document),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{stem}-proposal.md"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/api/documents/{document_id}/files/proposal.html", tags=["documents"])
async def print_document(document_id: str) -> HTMLResponse:
    document = _require_document(document_id)
    html = render_print_html(
        _document_markdown(document),
        f"{document.title or document.project_name} \u00b7 Proposal",
        _document_estimate(document),
        kind="proposal",
        # The client signs this page. It carries the studio's name, not the name
        # of the tool that produced it, and it is not titled as a quotation.
        brand=document.studio_name or "",
        doc_label="Proposal",
        reference_label="Proposal no." if document.reference else "Quotation ref.",
        reference_text=document.reference,
        cover_break=True,
        # The look it was built with, not the look Settings has today.
        design=document.design,
    )
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@router.get("/api/documents/{document_id}/files/proposal.pdf", tags=["documents"])
async def download_document_pdf(document_id: str) -> Response:
    document = _require_document(document_id)
    try:
        data = render_pdf(
            _document_markdown(document),
            f"{document.title or document.project_name} \u00b7 Proposal",
            _document_estimate(document),
            kind="proposal",
            doc_label="Proposal",
            cover_break=True,
            design=document.design,
        )
    except Exception as exc:  # pragma: no cover - a renderer bug, not user input
        deps.logger.exception("PDF rendering failed for proposal %s", document_id)
        raise HTTPException(
            status_code=500,
            detail=(
                "The PDF could not be produced. The error is in the API log; the markdown and "
                "print views of this proposal still work."
            ),
        ) from exc

    stem = deps.slugify(document.project_name or document.id, "proposal")
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{stem}-proposal.pdf"',
            "Cache-Control": "no-store",
        },
    )
