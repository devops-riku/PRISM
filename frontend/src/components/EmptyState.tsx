/**
 * The empty state is an invitation, not an apology for having nothing.
 */
export default function EmptyState() {
  return (
    <div className="border-t border-rule px-4 py-14 text-center sm:px-8">
      <p className="font-label text-[12px] font-medium uppercase tracking-[0.14em] text-void">
        No quotation on file
      </p>
      <p className="mx-auto mt-4 max-w-[420px] font-body text-[15px] leading-[1.6] text-ink">
        Nothing quoted yet. Describe the job above and PRISM will quote it.
      </p>
    </div>
  )
}
