# Deploy Emma AI on Fly.io - Step-by-Step Guide

This guide walks you through deploying Emma with Ollama to Fly.io using the free tier.

---

## Prerequisites

1. **Fly.io account** (free)
2. **Fly CLI** installed
3. **GitHub account** with Emma repository pushed

---

## Step 1: Install Fly CLI

### Windows (PowerShell):
```powershell
iwr https://fly.io/install.ps1 -useb | iex
```

### macOS/Linux:
```bash
curl -L https://fly.io/install.sh | sh
```

### Verify Installation:
```bash
fly version
```

---

## Step 2: Authenticate with Fly.io

```bash
fly auth login
```

This will open a browser window. Log in with your Fly.io account.

---

## Step 3: Initialize Fly.io Project

Navigate to your Emma repository:
```bash
cd C:\Users\ADMIN\OneDrive\Desktop\Ai\emma-ai
```

Initialize the project:
```bash
fly launch
```

You'll be prompted with questions:
- **App name**: Press Enter to accept `emma-ai` (or choose your own)
- **Region**: Choose a region close to you (e.g., `ord` for Chicago, `iad` for Virginia)
- **Dockerfile**: Enter `infrastructure/Dockerfile.render`
- **Docker context**: Press Enter for `.`
- **Deploy now**: Type `n` (we'll configure first)

---

## Step 4: Configure fly.toml

I've already created `fly.toml` in your repository with all the necessary configuration. 

If the `fly launch` command created a different `fly.toml`, replace it with the one I created.

---

## Step 5: Deploy to Fly.io

```bash
fly deploy
```

This will:
1. Build the Docker image with Ollama
2. Pull the `qwen3.5:2b` model automatically
3. Deploy to Fly.io infrastructure
4. Start Ollama and Emma together

**First deployment takes 10-15 minutes** (downloads Ollama + model).

---

## Step 6: Monitor Deployment

Watch the deployment progress:
```bash
fly logs
```

You should see:
- Ollama starting
- Model being pulled
- Emma backend starting
- Health checks passing

---

## Step 7: Access Emma

Once deployment is complete, Fly.io will provide a URL like:
```
https://emma-ai.fly.dev
```

Open this URL in your browser to access the Emma HUD.

---

## Step 8: Test Emma

Send a test message like "hello emma" to verify everything is working.

---

## Important Notes

### Free Tier Limits
- **3 shared-cpu-1x VMs**
- **3GB RAM total**
- **160GB bandwidth per month**
- **App sleeps after 10 minutes of inactivity** (auto-stop)

### Resource Allocation
The `fly.toml` configures:
- **1 CPU** (shared)
- **1GB RAM** (sufficient for Ollama + Emma)
- **Auto-stop after inactivity** (saves costs)
- **Auto-start on request** (wakes up when accessed)

### Performance
- **First request after sleep**: 10-30 seconds (cold start)
- **Subsequent requests**: Fast (while app is running)
- **Model**: `qwen3.5:2b` (good balance of speed/quality)

---

## Troubleshooting

### Issue 1: Deploy Fails - Out of Memory
**Error**: Build or deployment fails with memory error

**Solution**:
1. Check fly.toml memory setting
2. Increase to 2048MB if needed:
   ```toml
   [[vm]]
     cpu_kind = "shared"
     cpus = 1
     memory_mb = 2048
   ```

### Issue 2: Ollama Won't Start
**Error**: "Waiting for Ollama..." timeout

**Solution**:
1. Check logs: `fly logs`
2. Verify Ollama installation in Dockerfile
3. Check if port 11434 is accessible

### Issue 3: Emma Can't Connect to Ollama
**Error**: "No LLM provider available"

**Solution**:
1. Verify `EMMA_OLLAMA_URL=http://localhost:11434` in fly.toml
2. Check Ollama is running: `fly ssh` then `curl http://localhost:11434/api/tags`
3. Verify model is pulled: `fly ssh` then `ollama list`

### Issue 4: App Sleeps Too Quickly
**Problem**: App stops after 10 minutes of inactivity

**Solution**:
1. This is normal behavior on free tier
2. First request after sleep takes 10-30 seconds
3. You can disable auto-stop if needed (uses free credits faster):
   ```toml
   [http_service]
     auto_stop_machines = false
   ```

---

## Managing Your Deployment

### View Logs:
```bash
fly logs
```

### SSH into the running app:
```bash
fly ssh
```

### Check status:
```bash
fly status
```

### Scale up (if needed):
```bash
fly scale count 2
```

### Redeploy after code changes:
```bash
fly deploy
```

### Destroy the app:
```bash
fly apps destroy emma-ai
```

---

## Cost Summary

Fly.io Free Tier:
- **Cost**: $0 forever
- **Resources**: 3GB RAM, 3 CPUs
- **Bandwidth**: 160GB/month
- **Sleep**: Auto-stop after inactivity

**Emma specific costs**:
- **Ollama**: Free (local model)
- **Deepgram**: Free tier available (if you add API key)
- **Supabase**: Free tier available (if you add credentials)

---

## Next Steps After Deployment

1. **Test basic functionality**: Send "hello emma"
2. **Test LLM**: Ask a question requiring reasoning
3. **Test tools**: Try "list files in current directory"
4. **Monitor logs**: Watch for any errors
5. **Configure optional features**: Add Deepgram/Supabase if needed

---

## Alternative: Railway.app

If Fly.io doesn't work well, try Railway.app:
- $5 free credit
- Easier deployment
- Better Docker support
- See `FREE_DEPLOYMENT_OPTIONS.md` for details

---

## Summary

Deploying Emma to Fly.io:

1. ✅ Install Fly CLI
2. ✅ Authenticate: `fly auth login`
3. ✅ Initialize: `fly launch`
4. ✅ Use existing `fly.toml` configuration
5. ✅ Deploy: `fly deploy`
6. ✅ Access at `https://emma-ai.fly.dev`
7. ✅ Free forever with auto-stop for inactivity

**Important**: First deployment takes 10-15 minutes (downloads Ollama + model). Be patient!
