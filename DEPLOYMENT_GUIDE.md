# GymAI Deployment Guide

## 1. Project architecture

- Frontend: React + Vite + TypeScript deployed on Vercel
- Backend: FastAPI app deployed on Render
- Database: Neon PostgreSQL
- Email: Resend API
- Scheduler: GitHub Actions calling a protected internal FastAPI endpoint

## 2. Neon PostgreSQL setup

1. Sign up for Neon.
2. Create a new Postgres project.
3. Copy the connection string.
4. Example:
   ```env
   DATABASE_URL=postgresql://user:password@ep-xxx.us-east-2.aws.neon.tech/gymai?sslmode=require
   ```
5. Use the connection string in the Render backend environment variables.

## 3. Render backend setup

1. Create a new Render web service.
2. Set the root directory to `backend`.
3. Use Python runtime.
4. Build command:
   ```bash
   pip install -r requirements.txt
   ```
5. Start command:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```
6. Set environment variables from the backend `.env.example` with production values.

### Required backend environment variables

```env
ENVIRONMENT=production
DATABASE_URL=postgresql://user:password@host:5432/gymai?sslmode=require
SECRET_KEY=replace-with-a-long-random-secret
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=43200
CORS_ORIGINS=https://your-vercel-app.vercel.app
FRONTEND_URL=https://your-vercel-app.vercel.app
RESEND_API_KEY=
EMAIL_FROM=GymAI <noreply@your-domain.com>
ADMIN_API_KEY=replace-with-a-long-random-admin-key
SCHEDULER_API_KEY=replace-with-a-long-random-scheduler-key
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=https://your-render-backend.onrender.com/api/auth/google/callback
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
SCHEDULER_TIMEZONE=Asia/Kolkata
REMINDER_HOUR=21
REMINDER_MINUTE=0
REMINDER_ENABLED=false
```

## 4. Vercel frontend setup

1. Import the frontend folder into Vercel.
2. Set root directory to `frontend`.
3. Install command:
   ```bash
   npm ci
   ```
4. Build command:
   ```bash
   npm run build
   ```
5. Output directory:
   ```bash
   dist
   ```
6. Add environment variables:
   ```env
   VITE_API_URL=https://your-render-backend.onrender.com/api
   VITE_GOOGLE_CLIENT_ID=YOUR_CLIENT_ID.apps.googleusercontent.com
   ```

## 5. Resend setup

1. Create a Resend account.
2. Add and verify your domain.
3. Generate an API key.
4. Add the API key to Render as `RESEND_API_KEY`.
5. Set `EMAIL_FROM` to your verified sender address such as `GymAI <noreply@your-domain.com>`.
6. This replaces the SMTP-based email flow on Render free-tier hosting.

## 6. GitHub Actions scheduler

The workflow is defined at `.github/workflows/gymai-reminders.yml`.

It runs every day at 9:00 PM IST (15:30 UTC), and it also supports manual runs via `workflow_dispatch`.

### Required repository secrets

- `GYMAI_API_URL`: full Render backend URL, for example `https://your-render-backend.onrender.com`
- `GYMAI_SCHEDULER_KEY`: the same value as `SCHEDULER_API_KEY` on the backend

### Test the workflow manually

1. Go to GitHub repository Actions.
2. Select `GymAI Reminders`.
3. Run the workflow manually.
4. Confirm the backend endpoint returns a success response.

## 7. Google OAuth production configuration

1. Open Google Cloud Console.
2. Create or edit your OAuth client.
3. Add the frontend domain as an authorized JavaScript origin.
4. Add the backend callback URI:
   ```text
   https://your-render-backend.onrender.com/api/auth/google/callback
   ```
5. Ensure the frontend uses the same `VITE_GOOGLE_CLIENT_ID` value.

## 8. Gemini API configuration

1. Create a Gemini API key from Google AI Studio.
2. Put it under `GEMINI_API_KEY` on the backend.
3. Keep it private; do not expose it to the frontend.

## 9. Database initialization

1. Start the backend with the Neon database URL in the environment.
2. Confirm the app boots and creates the tables if missing.
3. Validate the health route:
   ```bash
   curl https://your-render-backend.onrender.com/api/health
   ```
4. Create an admin account using the existing local script if needed on a deploy shell.

## 10. Testing procedure

### Backend

```bash
cd backend
python3 -m compileall app
python3 - <<'PY'
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
print(client.get('/api/health').status_code)
PY
```

### Frontend

```bash
cd frontend
npm ci
npm run build
```

## 11. Redeploy procedure

1. Update backend env values on Render.
2. Trigger a new deploy.
3. Update Vercel env values if needed.
4. Confirm login, registration, forgot password, and workout generation work.
5. Run the GitHub reminder workflow manually once.

## 12. Troubleshooting

### CORS errors
- Check `CORS_ORIGINS` matches the deployed Vercel frontend exactly.

### JWT/auth errors
- Ensure `SECRET_KEY` is set and different from the development default.

### Email failures
- Confirm `RESEND_API_KEY` and `EMAIL_FROM` are valid.
- Ensure the sender domain is verified in Resend.

### Scheduler not firing
- Check GitHub secrets are configured.
- Check the Render backend is reachable and `SCHEDULER_API_KEY` matches.

### Database connection issues
- Ensure `sslmode=require` is included for Neon.
- Check that the database user credentials and host are correct.

## 13. Free-tier limits

- Render free web services sleep when idle.
- GitHub Actions is used for scheduled reminders because the Render backend cannot reliably run a local loop while sleeping.
- Neon free tier is suitable for a small production MVP.
- Resend free-tier limits apply; monitor usage.

## 14. Security checklist

- Never commit `.env` files.
- Keep all secrets in platform environment variables.
- Use secure random values for `SECRET_KEY`, `ADMIN_API_KEY`, and `SCHEDULER_API_KEY`.
- Restrict CORS to the production frontend domain.
- Keep AI keys on the backend only.
- Validate all external provider settings before public launch.

## 15. Final notes

This setup preserves the current app structure and feature set while making it deployable on the required free-tier stack. External services replace SMTP, local scheduler loops, and local-only project storage guarantees.
