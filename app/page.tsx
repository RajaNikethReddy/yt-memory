import Link from 'next/link'

export default function HomePage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-white px-6 text-center">
      <div className="max-w-xl">
        <span className="mb-4 inline-block rounded-full bg-indigo-50 px-3 py-1 text-sm font-medium text-indigo-600">
          Your YouTube Second Brain
        </span>

        <h1 className="mt-4 text-4xl font-extrabold tracking-tight text-gray-900 sm:text-5xl">
          Remember everything
          <br />
          you watch
        </h1>

        <p className="mt-4 text-lg text-gray-500">
          yt-memory turns YouTube videos into a personal knowledge base that
          remembers, connects, and evolves with you over time.
        </p>

        <div className="mt-8 flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
          <Link
            href="/signup"
            className="rounded-xl bg-indigo-600 px-6 py-3 text-sm font-semibold text-white shadow hover:bg-indigo-700"
          >
            Get started — it&apos;s free
          </Link>
          <Link
            href="/login"
            className="rounded-xl border border-gray-300 px-6 py-3 text-sm font-semibold text-gray-700 hover:bg-gray-50"
          >
            Sign in
          </Link>
        </div>
      </div>
    </main>
  )
}
