# Step-by-Step Guide: Host Emma AI on Render.com

This guide walks you through deploying Emma AI to Render.com, a free cloud hosting platform.

---

## Prerequisites

1. **GitHub account** with Emma AI repository pushed
2. **Render.com account** (free tier available)
3. **Optional API keys** for enhanced features:
   - Ollama Cloud model (gemma4:31b-cloud) - works with Ollama Cloud
   - Deepgram API key (for voice features)
   - Supabase credentials (for cloud memory sync)
   - Groq API key (only if you want to use Groq instead of Ollama Cloud)

---

## Step 1: Prepare Your Repository

### 1.1 Ensure `.env.example` is in your repo
Your `.env.example` should already be committed (from our previous work).

### 1.2 Add `render.yaml` to your repo
I've already created `render.yaml` in your repo root. This file tells Render how to build and run Emma.

### 1.3 Commit and push the new file
```bash
git add render.yaml
git commit -m "Add Render.com deployment configuration"
git push
```

---

## Step 2: Create Render Account

1. Go to https://render.com
2. Click **Sign Up**
3. Sign up with **GitHub** (recommended for easy repo connection)
4. Authorize Render to access your GitHub repositories

---

## Step 3: Connect Your Repository

1. After signing in, click **New +** → **Web Service**
2. You'll see a list of your GitHub repositories
3. Find and select **Freshoutlaw/Emma** (or your repo name)
4. If you don't see it, click **Configure account** → **Connect** to grant access

---

## Step 4: Configure the Web Service

### 4.1 Basic Settings

- **Name**: `emma-ai` (or any name you prefer)
- **Region**: Choose the region closest to you (e.g., Oregon, Frankfurt)
- **Branch**: `main`
- **Root Directory**: Leave empty (root of repo)
- **Runtime**: **Python** (should auto-detect from `requirements.txt`)
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`

### 4.2 Instance Type

- **Plan**: Select **Free** (starts with 512MB RAM, 0.1 CPU)
  - ⚠️ **Free tier limitations**: Emma may be slow or crash with complex tasks
  - 💡 **Recommendation**: Upgrade to **Starter ($7/mo)** for 512MB RAM + 0.5 CPU
  - 🚀 **Best performance**: **Standard ($25/mo)** for 2GB RAM + 1 CPU

### 4.3 Environment Variables

Render will auto-load the variables from `render.yaml`, but you need to add **sensitive keys** manually:

#### Required for Cloud LLM (Ollama Cloud - recommended):
Since Render doesn't have local Ollama, you have two options:

**Option A: Use Ollama Cloud (Recommended)**
1. No API key needed - Ollama Cloud works through your local Ollama binary
2. Emma will use `gemma4:31b-cloud` as configured in `.env.example`
3. Free tier available with rate limits

**Option B: Use Groq Cloud (Alternative)**
1. Get a free Groq API key: https://console.groq.com/keys
2. Add environment variable:
   - **Key**: `GROQ_API_KEY`
   - **Value**: Your Groq API key
   - **Sync**: Off
3. Uncomment `EMMA_CLOUD_MODEL` in environment variables if using Groq

#### Required for Voice Features (optional):
1. Add:
   - **Key**: `DEEPGRAM_API_KEY`
   - **Value**: Your Deepgram API key
   - **Sync**: Off

#### Required for Cloud Memory Sync (optional):
1. Add all four Supabase variables:
   - `EMMA_SUPABASE_URL`
   - `EMMA_SUPABASE_ANON_KEY`
   - `EMMA_SUPABASE_SERVICE_KEY`
   - `EMMA_SUPABASE_QUERY_DSN`
   - All with **Sync**: Off

### 4.4 Advanced Settings (if needed)

- **Health Check Path**: `/api/system/status`
- **Auto-Deploy**: Enable (deploys on every push to main branch)

---

## Step 5: Deploy

1. Click **Create Web Service**
2. Render will:
   - Clone your repository
   - Install dependencies from `requirements.txt`
   - Build the Docker image
   - Start the service
3. Wait for deployment (typically 2-5 minutes on first build)
4. You'll see a live URL like: `https://emma-ai.onrender.com`

---

## Step 6: Access Emma

1. Once deployment is complete, click your service URL
2. You should see the Emma HUD at `https://emma-ai.onrender.com`
3. Test by sending a message like "hello emma"

---

## Step 7: Configure Cloud LLM (Required for Render)

Since Render doesn't have Ollama pre-installed, you need a cloud LLM:

### Option A: Use Ollama Cloud (Recommended)
Emma is configured to use Ollama Cloud (`gemma4:31b-cloud`) by default:
- No API key required
- Works through Ollama's cloud service
- Free tier with rate limits
- Already configured in `.env.example`

### Option B: Use Groq Cloud (Alternative)
If you prefer Groq over Ollama Cloud:
1. Get a free Groq API key: https://console.groq.com/keys
2. Add `GROQ_API_KEY` as environment variable in Render
3. Emma will auto-detect and use Groq when available

---

## Step 8: Monitor Your Deployment

### View Logs
1. Go to your service in Render dashboard
2. Click **Logs** tab
3. Watch for errors or startup issues

### View Metrics
1. Click **Metrics** tab
2. Monitor CPU, memory, and response times

### Auto-Deploy
- Every push to `main` branch triggers automatic redeploy
- Can disable in **Settings** → **Auto-Deploy** if needed

---

## Common Issues & Solutions

### Issue 1: Build Fails - Module Not Found
**Error**: `ModuleNotFoundError: No module named 'xxx'`

**Solution**:
1. Check if all dependencies are in `requirements.txt`
2. Ensure version pins are compatible
3. Check logs for specific missing module

### Issue 2: Service Crashes - Out of Memory
**Error**: Service restarts repeatedly, logs show memory errors

**Solution**:
1. Upgrade from Free to Starter plan ($7/mo)
2. Or reduce Emma's memory usage by disabling heavy features:
   - Remove vision dependencies from `requirements.txt`
   - Set `EMMA_OLLAMA_NUM_CTX=2048` to limit context

### Issue 3: LLM Not Working
**Error**: "No LLM provider available"

**Solution**:
1. Ensure Ollama Cloud is configured (default: `gemma4:31b-cloud`)
2. If using Groq instead, add `GROQ_API_KEY` environment variable
3. Ensure your chosen provider has available quota
4. Check logs for connection errors

### Issue 4: Voice Features Not Working
**Error**: STT/TTS fails

**Solution**:
1. Add `DEEPGRAM_API_KEY` environment variable
2. Ensure Deepgram API key is valid
3. Voice features may be slow on free tier

### Issue 5: Supabase Connection Fails
**Error**: "Supabase RPC failed"

**Solution**:
1. Apply the schema to your Supabase project (SQL from earlier)
2. Verify all four Supabase environment variables are set
3. Check Supabase URL and keys are correct

---

## Performance Optimization Tips

### 1. Upgrade Your Plan
- **Free**: 512MB RAM, 0.1 CPU - Emma will be slow
- **Starter ($7/mo)**: 512MB RAM, 0.5 CPU - Good for basic tasks
- **Standard ($25/mo)**: 2GB RAM, 1 CPU - Recommended for full features

### 2. Reduce Model Context
Add to environment variables:
```
EMMA_OLLAMA_NUM_CTX=2048
EMMA_OLLAMA_KEEP_ALIVE=60
```

### 3. Disable Heavy Features
Edit `requirements.txt` to comment out:
- Vision: `# mediapipe>=0.10`, `# opencv-python>=4.9`
- Browser: `# playwright>=1.44`
- Desktop: `# pyautogui>=0.9.54`

### 4. Optimize LLM Configuration
Add to environment variables:
```
EMMA_OLLAMA_NUM_CTX=2048
EMMA_OLLAMA_KEEP_ALIVE=60
```

### 5. Use Ollama Cloud Only
Ensure `EMMA_OLLAMA_CLOUD_MODEL=gemma4:31b-cloud` is set (default configuration).

---

## Security Best Practices

1. **Never commit API keys** to GitHub
2. **Use Render's Environment Variables** for all secrets
3. **Set Sync: Off** for sensitive variables
4. **Enable Guardian consent mode** (already default)
5. **Monitor audit logs** regularly
6. **Use kill switch** if Emma misbehaves

---

## Cost Estimate

| Plan | Monthly Cost | Best For |
|------|-------------|-----------|
| Free | $0 | Testing, basic chat |
| Starter | $7 | Light usage, occasional tasks |
| Standard | $25 | Full features, regular use |
| Pro | $70 | Heavy usage, multiple users |

**Additional costs** (if using external services):
- Ollama Cloud: Free tier available, paid tiers for higher limits
- Groq: Free tier generous, paid tiers available (if using Groq instead)
- Deepgram: Free tier limited, paid tiers available
- Supabase: Free tier generous, paid tiers available

---

## Next Steps After Deployment

1. **Test basic functionality**: Send "hello emma"
2. **Test LLM**: Ask a question that requires reasoning
3. **Test tools**: Try "list files in current directory"
4. **Configure memory**: Add Supabase for persistent memory
5. **Set up monitoring**: Enable Render alerts
6. **Customize domain**: Add custom domain in Render settings (optional)

---

## Troubleshooting Checklist

If Emma isn't working on Render:

- [ ] Service deployed successfully (green checkmark)
- [ ] Can access the HUD at the URL
- [ ] Logs show no errors on startup
- [ ] Environment variables are set correctly
- [ ] API keys are valid and have quota
- [ ] Groq API key is set (for cloud LLM)
- [ ] Plan has enough RAM/CPU
- [ ] All dependencies in `requirements.txt`

---

## Need Help?

- **Render docs**: https://render.com/docs
- **Render community**: https://community.render.com
- **Emma README**: Check `README.md` in repo
- **Emma issues**: Open issue on GitHub

---

## Summary

Deploying Emma to Render takes about 10-15 minutes:

1. ✅ Add `render.yaml` to repo
2. ✅ Push to GitHub
3. ✅ Create Render account
4. ✅ Connect repository
5. ✅ Configure service (build/start commands)
6. ✅ Add environment variables (API keys)
7. ✅ Deploy
8. ✅ Access at `https://emma-ai.onrender.com`

**Important**: Start with the **Starter plan ($7/mo)** for reliable performance. The free tier is too limited for Emma's capabilities.
