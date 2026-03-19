export type VideoStatus = 'pending' | 'processing' | 'completed' | 'failed'

export interface Video {
  id: string
  user_id: string
  youtube_id: string
  title: string | null
  thumbnail_url: string | null
  duration_sec: number | null
  status: VideoStatus
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface UserProfile {
  user_id: string
  display_name: string | null
  interests: string[]
  weak_topics: string[]
  strong_topics: string[]
  created_at: string
  updated_at: string
}
