import SignupForm from '@/components/auth/SignupForm'

export default function SignupPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-sm rounded-2xl bg-white p-8 shadow-sm border border-gray-200">
        <h1 className="mb-6 text-2xl font-bold text-gray-900">
          Create your account
        </h1>
        <SignupForm />
      </div>
    </main>
  )
}
