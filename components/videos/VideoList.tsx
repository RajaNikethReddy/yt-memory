import type { Video } from '@/lib/database.types'
import VideoCard from './VideoCard'

export default function VideoList({ videos }: { videos: Video[] }) {
  if (videos.length === 0) {
    return (
      <p className="text-center text-sm text-gray-400 py-12">
        No videos yet. Paste a YouTube URL above to get started.
      </p>
    )
  }

  return (
    <ul className="flex flex-col gap-3">
      {videos.map((video) => (
        <li key={video.id}>
          <VideoCard video={video} />
        </li>
      ))}
    </ul>
  )
}
