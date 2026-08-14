# Railway.app Deployment Guide for Emma AI

This guide walks you through deploying Emma AI to Railway.app using Docker with Ollama.

---

## Why Railway.app?

✅ **$5 free credit** when you sign up
✅ **Better Docker support** than Render
✅ **More generous free tier** 
✅ **Easier deployment** with automatic detection
✅ **No manual environment variables** needed

---

## Prerequisites

1. **GitHub account** with Emma AI repository pushed
2. **Railway.app account** (free)
3. **Optional API keys**:
   - Deepgram API key (for voice features)
   - Supabase credentials (for cloud memory sync)

---

## Step 1: Sign Up for Railway

1. Go to https://railway.app
2. Click **Sign Up**
3. Sign up with **GitHub** (recommended)
4. You'll get **$5 free credit** automatically

---

## Step 2: Create New Project

1. After signing in, click **New Project**
2. Click **Deploy from GitHub repo**
3. Find and select **Freshoutlaw/Emma** (or your repo name)
4. If you don't see it, click **Configure GitHub** to grant access

---

## Step 3: Configure Deployment

Railway will automatically detect the Docker configuration from `railway.toml`:

- **Builder**: Dockerfile (from `infrastructure/Dockerfile`)
- **Environment Variables**: Auto-loaded from `railway.toml`
- **Model**: `phi3:mini` (small, efficient for free tier)

**No manual configuration needed!**

---

## Step 4: Deploy

1. Click **Deploy**
2. Railway will:
   - Build Docker image with Ollama
   - Pull `phi3:mini` model automatically
   - Start Ollama server
   - Start Emma backend
3. Wait for deployment (10-15 minutes on first build)
4. You'll see a live URL like: `https://emma-ai-production.up.railway.app`

---

## Step 5: Access Emma

1. Once deployment is complete, click your service URL
2. You should see the Emma HUD
3. Test by sending a message like "hello emma"

---

## Important Notes

### Free Tier Benefits
- **$5 free credit** (~1-2 months usage)
- **512MB RAM** minimum
- **Better resource allocation** than Render free
- **Automatic scaling** when needed

### Model Choice
- **phi3:mini**: Small but efficient (2GB)
- **Good for free tier**: Fits within resource limits
- **Upgrade later**: Can change to larger model with paid plan

### Performance
- **First deployment**: 10-15 minutes (downloads Ollama + model)
- **Subsequent deployments**: 2-5 minutes (cached)
- **Response time**: Fast while app is running

---

## Cost Summary

| Plan | Monthly Cost | Credits | Notes |
|------|-------------|---------|-------|
| Free | $0 | $5 included | 1-2 months usage |
| Hobby | $5/mo | $5 included | Good for personal use |
| Pro | $20/mo | $20 included | Better performance |

---

## Troubleshooting

### Issue 1: Build Fails - Out of Memory
**Error**: Build crashes during model pull

**Solution**:
1. Railway has better resource allocation than Render
2. If still fails, the free credit will help upgrade to Hobby plan

### Issue 2: Ollama Won't Start
**Error**: "Waiting for Ollama..." timeout

**Solution**:
1. Check Railway logs
2. Verify Ollama installation in Dockerfile
3. Railway has better resource isolation

### Issue 3: Emma Can't Connect to Ollama
**Error**: "No LLM provider available"

**Solution**:
1. Verify `EMMA_OLLAMA_URL=http://localhost:11434` in railway.toml
2. Check if Ollama is running in logs

---

## Next Steps After Deployment

1. **Test basic functionality**: Send "hello emma"
2. **Test LLM**: Ask a question requiring reasoning
3. **Test tools**: Try "list files in current directory"
4. **Monitor resources**: Check Railway metrics
5. **Upgrade if needed**: Use $5 credit for Hobby plan

---

## Comparison: Railway vs Render

| Feature | Railway | Render |
|---------|---------|--------|
| Free Credit | $5 included | None |
| Docker Support | Excellent | Good |
| Auto Detection | ✅ Yes | ❌ Manual |
| Environment Variables | ✅ Auto-loaded | ✅ Auto-loaded |
| Resource Allocation | Better | Limited |
| Setup Difficulty | Easy | Moderate |

---

## Summary

Deploying Emma to Railway:

1. ✅ Sign up and get $5 free credit
2. ✅ Deploy from GitHub repo
3. ✅ Automatic Docker detection
4. ✅ Environment variables auto-loaded
5. ✅ Ollama + model pull automatically
6. ✅ No manual configuration needed

**Recommendation**: Railway is the best free option with $5 credit and easier setup than Render.
