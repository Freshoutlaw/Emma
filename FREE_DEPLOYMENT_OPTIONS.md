# FREE Deployment Options for Emma AI (No Budget Required)

Since you have $0 budget, here are the best free options to host Emma with Ollama.

---

## 🥇 Option 1: Railway.app ($5 Free Credit) - RECOMMENDED

**Why it's the best free option:**
- $5 free credit when you sign up
- Better Docker support than Render
- More resources than Render free tier
- ~1-2 months of free usage
- Easy deployment

### Deployment Steps:

1. **Sign up for Railway**
   - Go to https://railway.app
   - Sign up with GitHub
   - You get $5 free credit automatically

2. **Create New Project**
   - Click **New Project**
   - Select **Deploy from Dockerfile**
   - Choose your GitHub repository

3. **Configure Docker**
   - Dockerfile path: `infrastructure/Dockerfile.render`
   - Context: `.`
   - Click **Deploy**

4. **Add Environment Variables**
   - Go to project settings → Variables
   - Add variables from `render.yaml` (except plan-related ones)
   - Key variables needed:
     ```
     EMMA_OLLAMA_URL=http://localhost:11434
     EMMA_LOCAL_MODEL=qwen3.5:2b
     EMMA_EMBEDDING_MODEL=nomic-embed-text
     EMMA_EMBEDDING_DIM=384
     ```

5. **Access Emma**
   - Railway provides a public URL
   - Open it in browser to access Emma HUD

### Pros:
- ✅ $5 free credit (1-2 months usage)
- ✅ Better Docker support
- ✅ More resources than Render free
- ✅ Easy deployment

### Cons:
- ❌ Credit runs out eventually
- ❌ Need to pay after credit expires

---

## 🥈 Option 2: Fly.io Free Tier

**Why it's good:**
- 3 shared-cpu-1x VMs
- 3GB RAM total
- 160GB bandwidth per month
- Truly free (no time limit)

### Deployment Steps:

1. **Install Fly CLI**
   ```bash
   curl -L https://fly.io/install.sh | sh
   ```

2. **Authenticate**
   ```bash
   fly auth login
   ```

3. **Initialize Project**
   ```bash
   cd emma-ai
   fly launch
   ```

4. **Configure**
   - Choose a region (e.g., ord for Chicago)
   - Dockerfile: `infrastructure/Dockerfile.render`
   - Add environment variables when prompted

5. **Deploy**
   ```bash
   fly deploy
   ```

### Pros:
- ✅ Truly free (no time limit)
- ✅ 3GB RAM (good for Ollama)
- ✅ Global edge network
- ✅ Good Docker support

### Cons:
- ❌ Requires CLI installation
- ❌ More complex setup
- ❌ Limited compute power

---

## 🥉 Option 3: Render Free Tier with Tiny Model

**Why it might work:**
- Uses `phi3:mini` (very efficient tiny model)
- Still 512MB RAM (limited but usable)
- No CLI required

### Deployment Steps:

1. **Use render-free.yaml**
   - I've created `render-free.yaml` for free tier
   - Uses `Dockerfile.render-free` with `phi3:mini`

2. **Deploy to Render**
   - Go to Render dashboard
   - Create new web service
   - Use `render-free.yaml` configuration
   - Runtime: Docker
   - Dockerfile: `infrastructure/Dockerfile.render-free`
   - Plan: Free

3. **Important Notes**
   - Free tier: 512MB RAM, 0.1 CPU
   - `phi3:mini` is ~2GB but very efficient
   - May still be slow or crash
   - No guarantee it will work

### Pros:
- ✅ Completely free
- ✅ No CLI required
- ✅ Easy setup

### Cons:
- ❌ Very limited resources
- ❌ May crash with complex tasks
- ❌ Slow performance
- ❌ Not guaranteed to work

---

## 📊 Comparison

| Option | Cost | RAM | Resources | Reliability |
|--------|------|-----|-----------|-------------|
| Railway | $5 free credit | Better | Good | ✅ High |
| Fly.io | Free forever | 3GB | Decent | ✅ High |
| Render Free | Free | 512MB | Poor | ⚠️ Uncertain |

---

## 🎯 My Recommendation

**Try Railway.app first** - it's the most reliable free option with $5 credit.

If Railway credit runs out, **Fly.io** is your best truly-free option.

**Render free tier** is a last resort - it may or may not work with the constraints.

---

## Quick Start: Railway.app (Recommended)

```bash
# 1. Go to https://railway.app
# 2. Sign up with GitHub (get $5 credit)
# 3. Click "New Project" → "Deploy from Dockerfile"
# 4. Select your Emma repository
# 5. Use Dockerfile: infrastructure/Dockerfile.render
# 6. Deploy and access Emma
```

That's it! Railway handles everything else.

---

## Files Created

I've created free-tier specific files:
- `infrastructure/Dockerfile.render-free` - Uses phi3:mini for Render free
- `render-free.yaml` - Render free tier configuration

**Not committed yet** - let me know if you want to try the Render free approach, or go with Railway/Fly.io instead.
