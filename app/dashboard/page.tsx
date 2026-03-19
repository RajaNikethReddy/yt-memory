import { createClient } from '@/lib/supabase/server'
import SubmitVideoForm from '@/components/videos/SubmitVideoForm'
import VideoList from '@/components/videos/VideoList'
import type { Video } from '@/lib/database.types'

export default async function DashboardPage() {
  const supabase = await createClient()

  const { data: videos } = await supabase
    .from('videos')
    .select('*')
    .order('created_at', { ascending: false })

  return (
    <main className="mx-auto max-w-2xl px-4 py-10 flex flex-col gap-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 mb-1">Your videos</h1>
        <p className="text-sm text-gray-500">
          Paste a YouTube URL to start building your second brain.
        </p>
      </div>

      <SubmitVideoForm />

      <VideoList videos={(videos as Video[]) ?? []} />
    </main>
  )
}
