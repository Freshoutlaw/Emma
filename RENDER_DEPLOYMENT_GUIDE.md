# Render.com Deployment Guide for Emma AI

This guide walks you through deploying Emma AI to Render.com using Docker with Ollama.

---

## Important: Ollama on Render

⚠️ **Ollama Cloud won't work on Render** because it requires the local Ollama binary to proxy requests. Render doesn't have Ollama installed.

**Solution**: Use Docker with local Ollama installed in the container.

---

## Prerequisites

1. **GitHub account** with Emma AI repository pushed
2. **Render.com account**
3. **Optional API keys**:
   - Deepgram API key (for voice features)
   - Supabase credentials (for cloud memory sync)

---

## Step 1: Choose Your Plan

### Free Tier (Experimental)
- Cost: $0
- Resources: 512MB RAM, 0.1 CPU
- Model: `phi3:mini` (tiny but efficient)
- **May not work** - very limited resources

### Starter Plan (Recommended)
- Cost: $7/mo
- Resources: 512MB RAM, 0.5 CPU
- Model: `qwen3.5:2b` (better performance)
- **Recommended** for reliable operation

### Standard Plan (Best)
- Cost: $25/mo
- Resources: 2GB RAM, 1 CPU
- Model: `qwen3.5:2b` or larger
- **Best performance**

---

## Step 2: Prepare Your Repository

Files are already configured:
- `render.yaml` - Render deployment configuration
- `infrastructure/Dockerfile` - Docker with Ollama (single file for all tiers)

---

## Step 3: Create Render Account

1. Go to https://render.com
2. Click **Sign Up**
3. Sign up with **GitHub** (recommended)
4. Authorize Render to access your GitHub repositories

---

## Step 4: Connect Your Repository

1. After signing in, click **New +** → **Web Service**
2. Find and select **Freshoutlaw/Emma** (or your repo name)
3. If you don't see it, click **Configure account** → **Connect**

---

## Step 5: Configure the Web Service

### 5.1 Basic Settings

- **Name**: `emma-ai`
- **Region**: Choose region closest to you
- **Branch**: `main`
- **Root Directory**: Leave empty
- **Runtime**: **Docker** (important - not Python)
- **Dockerfile Path**: `./infrastructure/Dockerfile`
- **Docker Context**: `.`
- **Plan**: Choose your plan (Free/Starter/Standard)

### 5.2 Environment Variables

Render will auto-load variables from `render.yaml`. No additional keys needed for basic Ollama setup.

**Optional keys** (add in Render dashboard if needed):
- `DEEPGRAM_API_KEY` - For voice features
- Supabase variables - For cloud memory sync

### 5.3 Advanced Settings

- **Health Check Path**: `/api/system/status`
- **Auto-Deploy**: Enable (deploys on every push to main branch)

---

## Step 6: Deploy

1. Click **Create Web Service**
2. Render will:
   - Build Docker image with Ollama
   - Pull the model automatically on startup
   - Start Ollama server
   - Start Emma backend
3. Wait for deployment (10-15 minutes on first build)
4. You'll see a live URL like: `https://emma-ai.onrender.com`

---

## Step 7: Access Emma

1. Once deployment is complete, click your service URL
2. You should see the Emma HUD at `https://emma-ai.onrender.com`
3. Test by sending a message like "hello emma"

---

## Performance by Plan

| Plan | RAM | CPU | Model | Performance |
|------|-----|-----|-------|-------------|
| Free | 512MB | 0.1 | phi3:mini | ⚠️ May not work |
| Starter | 512MB | 0.5 | qwen3.5:2b | ✅ Minimum viable |
| Standard | 2GB | 1.0 | qwen3.5:2b | ✅ Good performance |

---

## Troubleshooting

### Issue 1: Free Tier Deployment Fails
**Error**: Build or deployment fails with memory error

**Solution**: Upgrade to Starter plan ($7/mo)

### Issue 2: Ollama Won't Start
**Error**: "Waiting for Ollama..." timeout

**Solution**: Check logs, ensure enough RAM is allocated

### Issue 3: Emma Can't Connect to Ollama
**Error**: "No LLM provider available"

**Solution**: Verify `EMMA_OLLAMA_URL=http://localhost:11434` is set

---

## Cost Summary

| Plan | Monthly Cost | Notes |
|------|-------------|-------|
| Free | $0 | May not work with Ollama |
| Starter | $7 | Minimum for Ollama + Emma |
| Standard | $25 | Recommended for performance |

---

## Summary

Deploying Emma to Render:

1. ✅ Docker with Ollama installed in container
2. ✅ Model pulls automatically based on EMMA_LOCAL_MODEL
3. ✅ No Groq required - pure Ollama setup
4. ✅ Environment variables auto-loaded from render.yaml
5. ✅ Single Dockerfile works for all tiers (free/paid)

**Recommendation**: Start with Starter plan ($7/mo) for reliable performance.
