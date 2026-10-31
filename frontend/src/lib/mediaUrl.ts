/** Origin of the GymAI backend (the same server that answers /api), used for files it serves such as /media/... */
export function getBackendOrigin(): string {
  const configured = (import.meta.env.VITE_API_URL as string | undefined)?.trim()

  if (configured) {
    return configured.replace(/\/api\/?$/, '')
  }

  if (import.meta.env.PROD) {
    throw new Error('VITE_API_URL is required in production. Set it to the deployed backend URL.')
  }

  return 'http://localhost:8000'
}

export const BACKEND_ORIGIN = getBackendOrigin()

/** Guide illustration the backend serves for every exercise. */
export const guideArtUrl = (slug: string) => `${BACKEND_ORIGIN}/media/exercises/${slug}.svg`

/** Last-resort picture that always renders (no network): a dumbbell on a dark card. */
export const PLACEHOLDER_IMAGE = `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1c1c22"/><stop offset="1" stop-color="#0d0d10"/></linearGradient></defs><rect width="640" height="360" fill="url(#g)"/><g fill="none" stroke="#FF5A00" stroke-width="14" stroke-linecap="round"><path d="M250 180h140"/><path d="M236 140v80M214 156v48M404 140v80M426 156v48"/></g></svg>',
)}`
