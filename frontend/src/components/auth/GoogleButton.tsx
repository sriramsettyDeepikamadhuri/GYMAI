import {useToast} from '@/context/ToastContext';
import {getApiBaseUrl} from '@/lib/api';

const API = getApiBaseUrl().replace(/\/$/, '');

export function GoogleButton(){const{showToast}=useToast();return <div className="mt-5"><div className="mb-4 flex items-center gap-3 text-xs uppercase tracking-wider text-ink-faint"><span className="h-px flex-1 bg-surface-border"/>or<span className="h-px flex-1 bg-surface-border"/></div><button type="button" onClick={()=>{try{const googleStartUrl = new URL('/api/auth/google/start', `${API}/`).toString(); window.location.href = googleStartUrl;}catch{showToast('Google sign-in is unavailable right now.','error')}}} className="flex w-full items-center justify-center gap-3 rounded-full border border-surface-borderStrong bg-surface-raised px-4 py-3 text-sm font-medium text-ink hover:border-orange/50"><span className="grid h-5 w-5 place-items-center rounded-full bg-white font-bold text-black">G</span>Continue with Google</button></div>}
