import { DISPLAY, MONO_LABEL } from './tokens'

type QuotationHeaderProps = {
  /**
   * What this card is. Left empty on a prepared quotation, where the block is
   * the document's own masthead and the product's name belongs on it.
   */
  title?: string
  blurb?: string
  reference?: string
  quotationId?: string
  issued?: string
}

/**
 * The head of the card: what you are looking at, and — once there is one — the
 * quotation's number.
 *
 * Two jobs, told apart by whether a `title` was given.
 *
 * On a prepared quotation it is the document's masthead: PRISM, the line about
 * what PRISM does, and the reference. That is a thing being presented, and it
 * carries the name for the same reason a letterhead does.
 *
 * On the pad it is a form, and a form has no letterhead. The wordmark was
 * already in the top-left corner two lines above it, so setting it again at
 * 34px said the product's name twice and the form's purpose never. It labels
 * the work instead.
 *
 * The reference used to sit there as an empty ruled blank while the form was
 * still being filled in, which was the paper-pad conceit of the old direction.
 * A label for a value that does not exist yet is just a question the reader
 * cannot answer, so the whole block waits until there is a number to put in it.
 *
 * And the number it prints is the one on the documents - NEPT-0000018, the
 * studio's own prefix and sequence - not the twelve-hex storage id. They were
 * the same thing on this screen and different everywhere else, which made the
 * one number a client quotes back at you the one number the header did not
 * show.
 */
export default function QuotationHeader({
  title = '',
  blurb = '',
  reference = '',
  quotationId = '',
  issued = '',
}: QuotationHeaderProps) {
  const shown = reference || quotationId
  const masthead = !title

  return (
    // shrink-0 because on the pad this is a flex item in a card pinned to the
    // viewport, and a squashed heading is not a way to find another 20px.
    <header className="flex shrink-0 flex-wrap items-end justify-between gap-x-8 gap-y-4 border-b border-rule px-4 py-5 sm:px-8 sm:py-6">
      <div>
        <p
          className={`${DISPLAY} leading-none text-ink ${
            masthead ? 'text-[34px]' : 'text-[21px] tracking-[-0.02em]'
          }`}
        >
          {masthead ? 'PRISM' : title}
        </p>
        <p className="mt-2 font-body text-[13px] leading-[1.6] text-void">
          {masthead ? 'One scope in, two documents out.' : blurb}
        </p>
      </div>

      {shown ? (
        <div className="flex items-end gap-3">
          <span className={MONO_LABEL}>Quotation No.</span>
          <span className="min-w-[140px] border-b border-rule pb-[2px] font-label text-[13px] tracking-[0.04em] tabular-nums text-ink">
            {shown}
          </span>
        </div>
      ) : null}

      {issued ? (
        <p className="w-full border-t border-rule pt-3 font-label text-[12px] uppercase tracking-[0.14em] tabular-nums text-void">
          Issued {issued}
        </p>
      ) : null}
    </header>
  )
}
