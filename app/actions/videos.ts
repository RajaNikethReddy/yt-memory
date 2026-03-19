'use server'

import { createClient } from '@/lib/supabase/server'
import { revalidatePath } from 'next/cache'

function extractYouTubeId(url: string): string | null {
  try {
    const parsed = new URL(url)

    if (parsed.hostname === 'youtu.be') {
      return parsed.pathname.slice(1).split('?')[0] || null
    }

    if (
      parsed.hostname === 'www.youtube.com' ||
      parsed.hostname === 'youtube.com'
    ) {
      const v = parsed.searchParams.get('v')
      return v && v.length === 11 ? v : null
    }

    const pathMatch = parsed.pathname.match(
      /\/(embed|shorts|v)\/([a-zA-Z0-9_-]{11})/
    )
    return pathMatch ? pathMatch[2] : null
  } catch {
    return null
  }
}

export type SubmitVideoResult =
  | { success: true; videoId: string }
  | { success: false; error: string }

export async function submitVideo(
  _prev: SubmitVideoResult | null,
  formData: FormData
): Promise<SubmitVideoResult> {
  const supabase = await createClient()

  const {
    data: { user },
    error: authError,
  } = await supabase.auth.getUser()

  if (authError || !user) {
    return { success: false, error: 'You must be logged in to submit a video.' }
  }

  const rawUrl = formData.get('url')
  if (typeof rawUrl !== 'string' || !rawUrl.trim()) {
    return { success: false, error: 'Please enter a YouTube URL.' }
  }

  const youtubeId = extractYouTubeId(rawUrl.trim())
  if (!youtubeId) {
    return {
      success: false,
      error: 'That does not look like a valid YouTube URL.',
    }
  }

  const { data, error } = await supabase
    .from('videos')
    .insert({ user_id: user.id, youtube_id: youtubeId, status: 'pending' })
    .select('id')
    .single()

  if (error) {
    if (error.code === '23505') {
      return {
        success: false,
        error: 'You have already submitted this video.',
      }
    }
    return { success: false, error: 'Failed to save video. Please try again.' }
  }

  // Fire-and-forget: notify the FastAPI backend to start processing.
  // Deliberately NOT awaited — if the backend is unreachable, video submission
  // still succeeds. The video stays 'pending' and can be retried later.
  const backendUrl = process.env.BACKEND_URL
  if (backendUrl) {
    void fetch(`${backendUrl}/process-video`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_id: data.id }),
    }).catch((err) => {
      console.warn('[submitVideo] Backend notification failed (non-fatal):', err)
    })
  }

  revalidatePath('/dashboard')
  return { success: true, videoId: data.id }
}
