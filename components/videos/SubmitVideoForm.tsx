'use client'

import { useActionState } from 'react'
import { submitVideo, type SubmitVideoResult } from '@/app/actions/videos'

export default function SubmitVideoForm() {
  const [state, formAction, isPending] = useActionState<
    SubmitVideoResult | null,
    FormData
  >(submitVideo, null)

  return (
    <form action={formAction} className="flex flex-col gap-3">
      <div className="flex gap-2">
        <input
          type="url"
          name="url"
          required
          disabled={isPending}
          className="flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-50"
          placeholder="https://youtube.com/watch?v=..."
        />
        <button
          type="submit"
          disabled={isPending}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50 whitespace-nowrap"
        >
          {isPending ? 'Adding…' : 'Add video'}
        </button>
      </div>

      {state && !state.success && (
        <p role="alert" className="text-sm text-red-600">
          {state.error}
        </p>
      )}
      {state?.success && (
        <p className="text-sm text-green-600">Video queued for processing.</p>
      )}
    </form>
  )
}
