# Step-by-Step Guide: Host Emma AI on Render.com with Docker + Ollama

This guide walks you through deploying Emma AI to Render.com using **Docker with local Ollama**. This keeps your Ollama-only setup without requiring Groq.

---

## Why Docker + Local Ollama?

- **Ollama Cloud** requires the local Ollama binary to proxy requests to `ollama.com`
- **Render doesn't have Ollama** pre-installed
- **Solution**: Install Ollama in a Docker container alongside Emma
- **Model**: Uses `qwen3.5:2b` (small, fast, ~2GB) pulled automatically

---

## Prerequisites

1. **GitHub account** with Emma AI repository pushed
2. **Render.com account** (Starter plan $7/mo required for Ollama)
3. **Optional API keys**:
   - Deepgram API key (for voice features)
   - Supabase credentials (for cloud memory sync)

---

## Step 1: Prepare Your Repository

### 1.1 Verify Dockerfile.render exists
I've created `infrastructure/Dockerfile.render` which:
- Installs Ollama in the Docker container
- Pulls `qwen3.5:2b` model automatically on startup
- Runs Ollama server and Emma together

### 1.2 Update render.yaml
I've updated `render.yaml` to use Docker deployment:
- Changed runtime from `python` to `docker`
- Set Dockerfile path to `./infrastructure/Dockerfile.render`
- Updated environment variables for local Ollama
- Set plan to `starter` (required for Ollama)

### 1.3 Commit and push
```bash
git add infrastructure/Dockerfile.render render.yaml
git commit -m "Add Docker + Ollama deployment for Render"
git push
```

---

## Step 2: Create Render Account

1. Go to https://render.com
2. Click **Sign Up**
3. Sign up with **GitHub** (recommended)
4. Authorize Render to access your GitHub repositories

---

## Step 3: Connect Your Repository

1. After signing in, click **New +** → **Web Service**
2. Find and select **Freshoutlaw/Emma** (or your repo name)
3. If you don't see it, click **Configure account** → **Connect**

---

## Step 4: Configure the Web Service

### 4.1 Basic Settings

- **Name**: `emma-ai`
- **Region**: Choose region closest to you
- **Branch**: `main`
- **Root Directory**: Leave empty
- **Runtime**: **Docker** (important - not Python)
- **Dockerfile Path**: `./infrastructure/Dockerfile.render`
- **Docker Context**: `.`
- **Plan**: **Starter ($7/mo)** - Required for Ollama + Emma
  - ⚠️ **Free tier is too limited** - Ollama needs more resources
  - 💡 **Starter plan**: 512MB RAM, 0.5 CPU - Minimum viable
  - 🚀 **Standard plan ($25/mo)**: 2GB RAM, 1 CPU - Recommended

### 4.2 Environment Variables

Render will auto-load variables from `render.yaml`. No API keys needed for basic Ollama setup.

**Optional keys** (add in Render dashboard if needed):
- `DEEPGRAM_API_KEY` - For voice features
- Supabase variables - For cloud memory sync

### 4.3 Advanced Settings

- **Health Check Path**: `/api/system/status`
- **Auto-Deploy**: Enable (deploys on every push to main branch)

---

## Step 5: Deploy

1. Click **Create Web Service**
2. Render will:
   - Build Docker image with Ollama installed
   - Pull `qwen3.5:2b` model automatically on startup
   - Start Ollama server on port 11434
   - Start Emma backend on port 8000
3. Wait for deployment (10-15 minutes on first build - downloads Ollama + model)
4. You'll see a live URL like: `https://emma-ai.onrender.com`

---

## Step 6: Access Emma

1. Once deployment is complete, click your service URL
2. You should see the Emma HUD at `https://emma-ai.onrender.com`
3. Test by sending a message like "hello emma"

---

## Performance Considerations

### First Deployment vs. Subsequent

- **First deployment**: 10-15 minutes (downloads Ollama + model)
- **Subsequent deployments**: 2-5 minutes (Ollama cached in layers)

### Resource Usage

- **Model size**: `qwen3.5:2b` is ~2GB
- **RAM**: Ollama + Emma needs at least 512MB
- **Disk**: Model stored in container, needs enough disk space

### Plan Recommendations

| Plan | Monthly Cost | Performance | Recommended? |
|------|-------------|-------------|--------------|
| Free | $0 | Too limited for Ollama | ❌ No |
| Starter | $7 | Minimum viable | ⚠️ Borderline |
| Standard | $25 | Good performance | ✅ Yes |

---

## Troubleshooting

### Issue 1: Build Fails - Out of Memory
**Error**: Build crashes during model pull

**Solution**:
1. Upgrade to Standard plan ($25/mo)
2. Or use a smaller model in Dockerfile.render (e.g., `phi3:mini`)

### Issue 2: Ollama Won't Start
**Error**: "Waiting for Ollama..." timeout

**Solution**:
1. Check Docker logs for Ollama startup errors
2. Ensure enough RAM is allocated
3. Try increasing timeout in start.sh script

### Issue 3: Emma Can't Connect to Ollama
**Error**: "No LLM provider available"

**Solution**:
1. Verify `EMMA_OLLAMA_URL=http://localhost:11434` is set
2. Check Ollama is running: `curl http://localhost:11434/api/tags`
3. Check model is pulled: `ollama list`

### Issue 4: Deployment Times Out
**Error**: "Timed Out" after 15 minutes

**Solution**:
1. First deployment takes longer - wait up to 20 minutes
2. Check if build is progressing in logs
3. Consider upgrading plan for faster builds

---

## Alternative Cloud Providers

If Render doesn't work well with Docker + Ollama, consider:

### Option A: Railway.app
- Better Docker support
- More generous free tier
- Similar deployment process

### Option B: Fly.io
- Built for Docker deployments
- Global edge network
- Better resource isolation

### Option C: Self-hosted
- VPS (DigitalOcean, Linode, AWS Lightsail)
- Full control over environment
- Can install Ollama natively

---

## Cost Summary

| Service | Monthly Cost | Notes |
|---------|-------------|-------|
| Render Starter | $7 | Minimum for Ollama |
| Render Standard | $25 | Recommended |
| Ollama | Free | Local model is free |
| Deepgram (optional) | Free tier available | For voice |
| Supabase (optional) | Free tier available | For memory |

---

## Next Steps

1. **Test basic functionality**: Send "hello emma"
2. **Test LLM**: Ask a question requiring reasoning
3. **Test tools**: Try "list files in current directory"
4. **Monitor resources**: Check Render metrics for RAM/CPU usage
5. **Optimize if needed**: Consider Standard plan if performance is poor

---

## Summary

Deploying Emma with Docker + Ollama on Render:

1. ✅ Dockerfile.render installs Ollama in container
2. ✅ render.yaml configured for Docker deployment
3. ✅ Ollama pulls qwen3.5:2b model automatically
4. ✅ No Groq required - pure Ollama setup
5. ✅ Requires Starter plan ($7/mo) minimum

**Important**: This approach keeps your Ollama-only setup while making it work on Render via Docker.
