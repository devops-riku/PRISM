import { fileUrl } from '../lib/api'
import { formatDate } from '../lib/format'
import SheetHeader from './SheetHeader'
import MarkdownView from './MarkdownView'
import LineItemTable from './LineItemTable'
import type { Confidence, DocumentKind, Estimate, GeneratedFile, ProposalBundle } from '../types'

const CONFIDENCE_LABEL: Record<Confidence, string> = { low: 'Low', medium: 'Medium', high: 'High' }

function pick(
  files: GeneratedFile[],
  kind: DocumentKind,
  fallbackIndex: number,
): GeneratedFile | null {
  const list = Array.isArray(files) ? files : []
  return list.find((file) => file.kind === kind) || list[fallbackIndex] || null
}

type ResultSheetsProps = {
  bundle: ProposalBundle
}

/**
 * The pair of documents: one for the person paying, one for the person
 * building. Two cards rather than a torn pad — the client quotation on surface,
 * the developer requirements on the warmer secondary fill with an accent left
 * edge, so they read as a pair without either looking like a copy of the other.
 *
 * `.sheet` carries the arrival (ck-rise), and the 60ms stagger comes from the
 * `.sheet ~ .sheet` rule, which the spacer between them does not break.
 */
export default function ResultSheets({ bundle }: ResultSheetsProps) {
  // `Partial` rather than `Estimate`, because `|| {}` really can be the empty
  // object here and every read below already defaults for itself.
  const estimate: Partial<Estimate> = bundle.estimate || {}
  const currency = estimate.currency || 'PHP'
  const proposal = pick(bundle.files, 'proposal', 0)
  const requirements = pick(bundle.files, 'requirements', 1)

  const meta = [
    `Currency ${currency}`,
    estimate.market_region ? `Market ${estimate.market_region}` : '',
    estimate.confidence
      ? `Confidence ${CONFIDENCE_LABEL[estimate.confidence] || estimate.confidence}`
      : '',
    estimate.client && estimate.client.validity_days
      ? `Valid ${estimate.client.validity_days} days`
      : '',
    bundle.created_at ? `Issued ${formatDate(bundle.created_at)}` : '',
  ].filter(Boolean)

  return (
    <section aria-label="Prepared quotation">
      <article aria-labelledby="sheet-original" className="sheet p-6 sm:p-10">
        <SheetHeader
          headingId="sheet-original"
          label="Original · For the client"
          filename={proposal ? proposal.filename : 'quotation.md'}
          downloadHref={fileUrl(bundle.id, 'proposal', 'md')}
          printHref={fileUrl(bundle.id, 'proposal', 'html')}
          pdfHref={fileUrl(bundle.id, 'proposal', 'pdf')}
        />

        {meta.length > 0 ? (
          <p className="mb-8 font-label text-[12px] uppercase tracking-[0.14em] text-void">
            {meta.join('  ·  ')}
          </p>
        ) : null}

        <MarkdownView markdown={proposal ? proposal.markdown : ''} tone="original" />

        <LineItemTable lineItems={estimate.line_items} cost={estimate.cost} currency={currency} />
      </article>

      <div role="presentation" className="h-8" />

      <article aria-labelledby="sheet-duplicate" className="sheet border-l-4 border-l-ballpoint bg-duplicate p-6 sm:p-10">
        <SheetHeader
          headingId="sheet-duplicate"
          label="Duplicate · For the developer"
          filename={requirements ? requirements.filename : 'requirements.md'}
          downloadHref={fileUrl(bundle.id, 'requirements', 'md')}
          printHref={fileUrl(bundle.id, 'requirements', 'html')}
          pdfHref={fileUrl(bundle.id, 'requirements', 'pdf')}
        />

        <MarkdownView markdown={requirements ? requirements.markdown : ''} tone="duplicate" />
      </article>
    </section>
  )
}
