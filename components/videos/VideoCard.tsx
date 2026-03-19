import type { Video, VideoStatus } from '@/lib/database.types'

const statusConfig: Record<
  VideoStatus,
  { label: string; className: string }
> = {
  pending:    { label: 'Queued',     className: 'bg-yellow-100 text-yellow-800' },
  processing: { label: 'Processing', className: 'bg-blue-100 text-blue-800' },
  completed:  { label: 'Ready',      className: 'bg-green-100 text-green-800' },
  failed:     { label: 'Failed',     className: 'bg-red-100 text-red-800' },
}

export default function VideoCard({ video }: { video: Video }) {
  const { label, className } = statusConfig[video.status]
  const date = new Date(video.created_at).toLocaleDateString()

  return (
    <div className="flex items-center justify-between rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex flex-col gap-1 min-w-0">
        <p className="truncate text-sm font-medium text-gray-900">
          {video.title ?? video.youtube_id}
        </p>
        <p className="text-xs text-gray-400">Added {date}</p>
      </div>
      <span
        className={`ml-4 shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium ${className}`}
      >
        {label}
      </span>
    </div>
  )
}
