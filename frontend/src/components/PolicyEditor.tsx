import type { PolicyClause } from '../types'
import { ACTION, MONO_LABEL, WELL } from './tokens'

/**
 * The terms every proposal carries.
 *
 * These are the one text in PRISM the model never touches. Everything else on a
 * proposal is written or printed; this is typed by whoever will be asked what it
 * meant. The editor is deliberately plain — a textarea and a switch — because
 * the value here is the wording, not the tooling around it.
 *
 * Leave the list empty and PRISM uses its recommended set rather than sending a
 * proposal with no terms at all. Turning a clause off removes it from every
 * proposal built afterwards; proposals already built keep the wording they were
 * built with.
 */

type PolicyEditorProps = {
  clauses?: PolicyClause[]
  onChange: (clauses: PolicyClause[]) => void
  fallbackCount?: number
}

export default function PolicyEditor({
  clauses = [],
  onChange,
  fallbackCount = 0,
}: PolicyEditorProps) {
  const set = (index: number, patch: Partial<PolicyClause>) =>
    onChange(clauses.map((clause, i) => (i === index ? { ...clause, ...patch } : clause)))

  const remove = (index: number) => onChange(clauses.filter((_, i) => i !== index))

  const add = () => onChange([...clauses, { id: '', title: '', body: '', enabled: true }])

  const enabled = clauses.filter((clause) => clause.enabled !== false).length

  return (
    <div>
      <p className="max-w-[70ch] font-body text-[13px] leading-[1.6] text-void">
        Printed at the end of every proposal, word for word. PRISM never writes or rewords these —
        nobody signs prose whose author cannot be asked what it meant. A proposal keeps the wording
        it was built with, so editing a clause today does not change one you sent last month.
      </p>

      {clauses.length === 0 ? (
        <div className="mt-4 rounded-lg border border-rule bg-duplicate px-4 py-3">
          <p className={MONO_LABEL}>Using the recommended set</p>
          <p className="mt-1 max-w-[70ch] font-body text-[13px] leading-[1.6] text-void">
            {fallbackCount} clauses covering validity, payment, scope changes, ownership,
            confidentiality and the Data Privacy Act, warranty, support, acceptance, third-party
            costs, termination, force majeure and governing law. Add one below to take over the list
            — once you do, the recommended set is no longer used.
          </p>
        </div>
      ) : (
        <p className={`${MONO_LABEL} mt-4`}>
          {enabled} of {clauses.length} clauses will be printed
        </p>
      )}

      <div className="mt-4 space-y-3">
        {clauses.map((clause, index) => (
          <div
            key={index}
            className={`rounded-lg border px-4 py-3 ${
              clause.enabled === false ? 'border-hairline bg-duplicate/40' : 'border-rule bg-paper'
            }`}
          >
            <div className="flex flex-wrap items-center gap-3">
              <input
                aria-label={`Title of clause ${index + 1}`}
                value={clause.title || ''}
                onChange={(event) => set(index, { title: event.target.value })}
                placeholder="Validity"
                className={`${WELL} min-w-[12rem] flex-1 py-2`}
              />
              <label className="flex cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  checked={clause.enabled !== false}
                  onChange={(event) => set(index, { enabled: event.target.checked })}
                  className="h-4 w-4 accent-ballpoint"
                />
                <span className="font-label text-[12px] uppercase tracking-[0.14em] text-void">
                  Printed
                </span>
              </label>
              <button type="button" className={ACTION} onClick={() => remove(index)}>
                Remove
              </button>
            </div>

            <textarea
              aria-label={`Wording of clause ${index + 1}`}
              value={clause.body || ''}
              onChange={(event) => set(index, { body: event.target.value })}
              rows={4}
              placeholder="The clause, exactly as it should appear in the proposal."
              className={`${WELL} mt-2 leading-[1.6]`}
            />
          </div>
        ))}
      </div>

      <button type="button" className={`${ACTION} mt-4`} onClick={add}>
        Add a clause
      </button>
    </div>
  )
}
